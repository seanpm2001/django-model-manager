import copy
import json
import requests
import socket

from django.conf import settings
from django.utils.translation import ugettext_lazy as _
from horizon import workflows

from .forms import Fieldset, CharField, BooleanField, IPField, ChoiceField 

'''
We can get cookiecutter.json using one of the private get_context methods, for example: _get_context_github

{
    "cluster_name"                              : "deployment_name",
    "cluster_domain"                            : "deploy-name.local",
    "public_host"                               : "${_param:openstack_proxy_address}",
    "reclass_repository"                        : "https://github.com/Mirantis/mk-lab-salt-model.git",

    "deploy_network_netmask"                    : "255.255.255.0",
    "deploy_network_gateway"                    : "",
    "control_network_netmask"                   : "255.255.255.0",
    ...
}

generate_context method uses get_context method of choice and transforms remote JSON into form fields schema:

    INFRA_JSON_URL = 'https://api.github.com/repos/Mirantis/mk2x-cookiecutter-reclass-model/contents/cluster_product/infra/cookiecutter.json'

    ctx = generate_context('github', 'infra', 'Infra', **{'url': INFRA_JSON_URL})
    print ctx

Results in:
    [{
        'fieldset_name': 'infra',
        'fieldset_label': _('Infra'),
        'fields': {
            'deploy_network_netmask': {'field_template': 'IP', 'kwargs': {'initial': '255.255.255.0'}},
            'deploy_network_gateway': {'field_template': 'IP'},
            'control_network_netmask': {'field_template': 'IP', 'kwargs': {'initial': '255.255.255.0'}},
            'dns_server01': {'field_template': 'IP', 'kwargs': {'initial': '8.8.8.8'}},
            'dns_server02': {'field_template': 'IP', 'kwargs': {'initial': '8.8.4.4'}},
            'control_vlan': {'field_template': 'TEXT', 'kwargs': {'initial': '10'}},
            'tenant_vlan': {'field_template': 'TEXT', 'kwargs': {'initial': '20'}},
            ...
        }
    }]
    
'''

INFRA_JSON_URL = 'https://api.github.com/repos/Mirantis/mk2x-cookiecutter-reclass-model/contents/cluster_product/infra/cookiecutter.json'


def _get_context_github(url):
    s = requests.Session()
    token = getattr(settings, 'GITHUB_TOKEN', None)
    s.headers.update({'Accept': 'application/vnd.github.v3.raw'})
    if token:
        s.headers.update({'Authorization': 'token ' + str(token)})
    r = s.get(url)
    ctx = json.loads(r.text)

    return ctx


def _is_ipaddress(addr):
    try:
        socket.inet_aton(addr)
        return True
    except socket.error:
        return False


def generate_context(source, name, label, **kwargs):
    ctx = {}
    if 'github' in source:
        url = kwargs.get('url')
        ctx = [{
            'fieldset_name': name,
            'fieldset_label': label,
            'fields': {}
        }]
        remote_ctx = _get_context_github(url)
        if isinstance(remote_ctx, dict):
            fields = ctx[0]['fields']
            for field, value in remote_ctx.items():
                params = {}
                params['field_template'] = 'TEXT'
                if value:
                    if _is_ipaddress(value):
                        params['field_template'] = 'IP'
                    else:
                        params['field_template'] = 'TEXT'
                    params['kwargs'] = {'initial': value}
                fields[field] = params

    return ctx


class GeneratedAction(workflows.Action):

    FIELDS = {
        "TEXT": {
            "class": CharField,
            "args": tuple(),
            "kwargs": {
                "max_length": 255,
                "label": "",
                "required": True,
                "help_text": ""
            }
        },
        "IP": {
            "class": IPField,
            "args": tuple(),
            "kwargs": {
                "label": "",
                "required": True,
                "mask": True
            }
        },
        "BOOL": {
            "class": BooleanField,
            "args": tuple(),
            "kwargs": {
                "label": "",
                "required": False
            }
        },
        "CHOICE": {
            "class": ChoiceField,
            "args": tuple(),
            "kwargs": {
                "label": "",
                "choices": [],
                "required": False
            }
        }
    }

    @staticmethod
    def deslugify(string):
        return str(string).replace('_', ' ').capitalize()

    def generate_fields(self, ctx):
        # iterate over fieldsets in context data
        for fieldset in ctx:
            fieldset_name = fieldset.get('name')
            fieldset_label = fieldset.get('label')
            fields = fieldset.get('fields')
            # create fieldset
            self.fields["fieldset_" + fieldset_name] = Fieldset(
                name=fieldset_name,
                label=fieldset_label
            )
            # iterate over fields dictionary
            for field in fields:
                # get field schema from FIELDS and set params
                field_templates = copy.deepcopy(self.FIELDS)
                field_template = field_templates[field['type']]
                field_cls = field_template['class']
                field_args = field_template['args']
                field_kw = field_template['kwargs']
                # set kwargs
                field_kw['fieldset'] = fieldset_name
                field_kw['label'] = field.get('label', None) if 'label' in field else self.deslugify(field['name'])
                field_kw['help_text'] = field.get('help_text', None)
                field_kw['initial'] = field.get('initial', None)
                if 'CHOICE' in field['type']:
                    field_kw['choices'] = field['choices']
                # declare field on self
                self.fields[field['name']] = field_cls(*field_args, **field_kw)


class GeneratedStep(workflows.Step):
    source_context = {}

    def __init__(self, *args, **kwargs):
        super(GeneratedStep, self).__init__(*args, **kwargs)
        ctx = self.source_context
        # get lists of fields
        field_lists = [x['fields'] for x in ctx]
        # flatten the lists
        field_list = [item for sublist in field_lists for item in sublist]
        contributes = list(self.contributes)
        for field in field_list:
            contributes.append(field['name'])
        self.contributes = tuple(contributes)

    def contribute(self, data, context):
        super(GeneratedStep, self).contribute(data, context)
        # update shared context with option Bool values according to choices made in ChoiceList fields
        choice_fields = [obj for obj in self.action.fields.values() if hasattr(obj, 'choices')]
        choices = [chc[0] for fld in choice_fields for chc in fld.choices]
        for choice in choices:
            context[choice] = True if choice in context.values() else False
        return context

