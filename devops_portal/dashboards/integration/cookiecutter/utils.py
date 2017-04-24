import copy, bcrypt, json, requests, socket, yaml

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
from django import forms
from django import http
from django.conf import settings
from django.utils.translation import ugettext_lazy as _
from horizon import workflows
from ipaddress import IPv4Network
from jinja2 import Environment, meta, exceptions
from os import urandom

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

####################################
# GET CONTEXT FROM REMOTE LOCATION #
####################################

INFRA_JSON_URL = 'https://api.github.com/repos/Mirantis/mk2x-cookiecutter-reclass-model/contents/cluster_product/infra/cookiecutter.json'
CICD_JSON_URL = 'https://api.github.com/repos/Mirantis/mk2x-cookiecutter-reclass-model/contents/cluster_product/cicd/cookiecutter.json'
KUBERNETES_JSON_URL = 'https://api.github.com/repos/Mirantis/mk2x-cookiecutter-reclass-model/contents/cluster_product/kubernetes/cookiecutter.json'
OPENCONTRAIL_JSON_URL = 'https://api.github.com/repos/Mirantis/mk2x-cookiecutter-reclass-model/contents/cluster_product/opencontrail/cookiecutter.json'
OPENSTACK_JSON_URL = 'https://api.github.com/repos/Mirantis/mk2x-cookiecutter-reclass-model/contents/cluster_product/openstack/cookiecutter.json'
STACKLIGHT_JSON_URL = 'https://api.github.com/repos/Mirantis/mk2x-cookiecutter-reclass-model/contents/cluster_product/stacklight/cookiecutter.json'


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
            'name': name,
            'label': label,
            'fields': []
        }]
        remote_ctx = _get_context_github(url)
        if isinstance(remote_ctx, dict):
            fields = ctx[0]['fields']
            bool_strings = ['true', 'True', 'false', 'False']
            for field, value in remote_ctx.items():
                params = {}
                params['name'] = field
                params['type'] = 'TEXT'
                if value:
                    if _is_ipaddress(value):
                        params['type'] = 'IP'
                    elif value in bool_strings:
                        params['type'] = 'BOOL'
                    else:
                        params['type'] = 'TEXT'
                    params['initial'] = value
                fields.append(params)

    return ctx

######################################################
# WORKFLOW ACTION AND STEP WITH AUTOGENERATED FIELDS #
######################################################

# Custom Jinja2 filters

def subnet(subnet, host_ip):
    """
    Create network object and get host by index

    Example:

        Context
        -------
        {'my_subnet': '192.168.1.0/24'}

        Template
        --------
        {{ my_subnet|subnet(1) }}

        Output
        ------
        192.168.1.1
    """
    if not subnet:
        return ""

    if '/' not in subnet:
        subnet = str(subnet) + '/24'

    try:
        network = IPv4Network(unicode(subnet))
        idx = int(host_ip) - 1
        ipaddr = str(list(network.hosts())[idx])
    except IndexError:
        ipaddr = _("Host index is out of range of available addresses")
    except:
        ipaddr = subnet.split('/')[0]

    return ipaddr


def netmask(subnet):
    """
    Create network object and get netmask

    Example:

        Context
        -------
        {'my_subnet': '192.168.1.0/24'}

        Template
        --------
        {{ my_subnet|netmask }}

        Output
        ------
        255.255.255.0
    """
    if not subnet:
        return ""

    if '/' not in subnet:
        subnet = str(subnet) + '/24'

    try:
        network = IPv4Network(unicode(subnet))
        netmask = str(network.netmask)
    except:
        netmask = "Cannot determine network mask"

    return netmask


def generate_password(length):
    """
    Generate password of defined length

    Example:

        Template
        --------
        {{ 32|generate_password }}

        Output
        ------
        Jda0HK9rM4UETFzZllDPbu8i2szzKbMM
    """
    chars = "aAbBcCdDeEfFgGhHiIjJkKlLmMnNpPqQrRsStTuUvVwWxXyYzZ1234567890!@#$"

    return "".join(chars[ord(c) % len(chars)] for c in urandom(length))


def hash_password(password):
    """
    Hash password

    Example:

        Context
        -------
        {'some_password': 'Jda0HK9rM4UETFzZllDPbu8i2szzKbMM'}

        Template
        --------
        {{ some_password|hash_password }}

        Output
        ------
        $2b$12$HXXew12E9mN3NIXv/egSDurU.dshYQRepBoeY.6bfbOOS5IyFVIBa
    """
    salt = bcrypt.gensalt()

    return bcrypt.hashpw(bytes(password), salt)


CUSTOM_FILTERS = [
    ('subnet', subnet),
    ('generate_password', generate_password),
    ('hash_password', hash_password),
    ('netmask', netmask)
]


def generate_ssh_keypair():
    private_key_obj = rsa.generate_private_key(backend=default_backend(), public_exponent=65537, \
        key_size=2048)
    
    public_key_obj = private_key_obj.public_key()
    
    public_key = public_key_obj.public_bytes(
        serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
    
    private_key = private_key_obj.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption())
    
    private_key_str = private_key.decode('utf-8')
    public_key_str = public_key.decode('utf-8')

    return (private_key_str, public_key_str)    
    
    
CUSTOM_FUNCTIONS = [
    ('generate_ssh_keypair', generate_ssh_keypair)
]

# Extended workflow classes

class GeneratedAction(workflows.Action):
    """ TODO: Document this class
    """
    source_context = ""
    field_templates = {
        "TEXT": {
            "class": CharField,
            "args": tuple(),
            "kwargs": {
                "max_length": 255,
                "label": "",
                "initial": "",
                "required": True,
                "help_text": ""
            }
        },
        "LONG_TEXT": {
            "class": CharField,
            "args": tuple(),
            "kwargs": {
                "label": "",
                "initial": "",
                "required": True,
                "widget": forms.Textarea(attrs={'rows': '20'}),
                "help_text": ""
            }
        },
        "IP": {
            "class": IPField,
            "args": tuple(),
            "kwargs": {
                "label": "",
                "initial": "",
                "required": True,
                "mask": False
            }
        },
        "BOOL": {
            "class": BooleanField,
            "args": tuple(),
            "kwargs": {
                "label": "",
                "initial": False,
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

    def __init__(self, request, context, *args, **kwargs):
        super(GeneratedAction, self).__init__(
            request, context, *args, **kwargs)

        rendered_context = self.render_context(context)
        for fieldset in rendered_context:
            if not self.requirements_met(fieldset, context):
                continue

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
                if not self.requirements_met(field, context):
                    continue

                # get field schema from FIELDS and set params
                field_templates = copy.deepcopy(self.field_templates)
                field_template = field_templates[field['type']]
                field_cls = field_template['class']
                field_args = field_template['args']
                field_kw = field_template['kwargs']
                # set kwargs
                field_kw['fieldset'] = fieldset_name
                field_kw['label'] = field.get('label', None) if 'label' in field else self.deslugify(field['name'])
                field_kw['help_text'] = field.get('help_text', None)
                field_kw['initial'] = field.get('initial', None)
                # template specific params
                if 'CHOICE' in field['type']:
                    field_kw['choices'] = field['choices']
                if 'IP' in field['type'] and 'mask' in field:
                    field_kw['mask'] = field['mask']
                # declare field on self
                self.fields[field['name']] = field_cls(*field_args, **field_kw)
                if field.get('readonly', False):
                    try:
                        self.fields[field['name']].widget.attrs['readonly'] = True
                    except:
                        pass
                if field.get('hidden', False):
                    self.fields[field['name']].widget = forms.HiddenInput()
                # workaround for empty strings in inital data after ``contribute`` is defined
                # TODO: find out why this is happening
                if field['name'] in self.initial and (self.initial[field['name']] == '' or self.initial[field['name']] == None):
                    self.initial[field['name']] = field.get('initial', None)

    @staticmethod
    def deslugify(string):
        return str(string).replace('_', ' ').capitalize()

    def render_context(self, context):
        env = Environment()
        for fltr in CUSTOM_FILTERS:
            env.filters[fltr[0]] = fltr[1]
        for fnc in CUSTOM_FUNCTIONS:
            env.globals[fnc[0]] = fnc[1]
        tmpl = env.from_string(self.source_context)
        parsed_source = env.parse(self.source_context)
        tmpl_ctx_keys = meta.find_undeclared_variables(parsed_source)
        for key in tmpl_ctx_keys:
            if key not in env.globals:
                if (not key in context) or (key in context and context[key] == None):
                    context[key] = ""
        try:
            ctx = yaml.load(tmpl.render(context))
        except:
            ctx = yaml.load(self.source_context)
        if not isinstance(ctx, list):
            return []
        return ctx

    def requirements_met(self, item, context):
        """Return True if all requirements for this field/fieldset are met
        """
        if context and 'requires' in item:
            for req in item['requires']:
                key = req.keys()[0]
                value = req.values()[0]
                if (not key in context) or (key in context and not value == context[key]):
                    return False
        if context and 'requires_or' in item:
            score = 0
            for req in item['requires_or']:
                key = req.keys()[0]
                value = req.values()[0]
                if key in context and value == context[key]:
                    score += 1
            if score == 0:
                return False

        return True


class GeneratedStep(workflows.Step):
    """ TODO: Document this class
    """
    template_name = "integration/cookiecutter/workflow/_workflow_step_with_fieldsets.html"
    depends_on = tuple()
    contributes = tuple()
    source_context = ""

    def __init__(self, *args, **kwargs):
        super(GeneratedStep, self).__init__(*args, **kwargs)
        ctx = self.render_context()
        # get lists of fields
        field_lists = [x['fields'] for x in ctx]
        # flatten the lists
        field_list = [item for sublist in field_lists for item in sublist]
        contributes = list(self.contributes)
        for field in field_list:
            if field['name'] not in contributes:
                contributes.append(field['name'])
        self.contributes = tuple(contributes)

    #def _verify_contributions(self, context):
    #    return True

    def contribute(self, data, context):
        super(GeneratedStep, self).contribute(data, context)
        # update shared context with option Bool values according to choices made in ChoiceList fields
        choice_fields = [obj for obj in self.action.fields.values() if hasattr(obj, 'choices')]
        choices = [chc[0] for fld in choice_fields for chc in fld.choices]
        for choice in choices:
            context[choice] = True if choice in context.values() else False
        return context

    def render_context(self):
        context = {}
        env = Environment()
        for fltr in CUSTOM_FILTERS:
            env.filters[fltr[0]] = fltr[1]
        for fnc in CUSTOM_FUNCTIONS:
            env.globals[fnc[0]] = fnc[1]
        tmpl = env.from_string(self.source_context)
        parsed_source = env.parse(self.source_context)
        tmpl_ctx_keys = meta.find_undeclared_variables(parsed_source)
        for key in tmpl_ctx_keys:
            if key not in env.globals:
                context[key] = ""
        try:
            ctx = yaml.load(tmpl.render(context))
        except:
            ctx = yaml.load(self.source_context)
        if not isinstance(ctx, list):
            return []
        return ctx


class AsyncWorkflowView(workflows.WorkflowView):
    """
    Overrides default WF functionality
    """
    def render_next_steps(self, request, workflow, start, end):
        """render next steps

        this allows change form content on the fly

        """
        rendered = {}

        request = copy.copy(self.request)
        # patch request method, because we want render new form without
        # validation
        request.method = "GET"

        new_workflow = self.get_workflow_class()(
            request,
            context_seed=workflow.context,
            entry_point=workflow.entry_point)

        for step in new_workflow.steps[end:]:
            rendered[step.get_id()] = step.render()

        return rendered

    def post(self, request, *args, **kwargs):
        """Handler for HTTP POST requests."""
        context = self.get_context_data(**kwargs)
        workflow = context[self.context_object_name]
        try:
            # Check for the VALIDATE_STEP* headers, if they are present
            # and valid integers, return validation results as JSON,
            # otherwise proceed normally.
            validate_step_start = int(self.request.META.get(
                'HTTP_X_HORIZON_VALIDATE_STEP_START', ''))
            validate_step_end = int(self.request.META.get(
                'HTTP_X_HORIZON_VALIDATE_STEP_END', ''))
        except ValueError:
            # No VALIDATE_STEP* headers, or invalid values. Just proceed
            # with normal workflow handling for POSTs.
            pass
        else:
            # There are valid VALIDATE_STEP* headers, so only do validation
            # for the specified steps and return results.
            data = self.validate_steps(request, workflow,
                                       validate_step_start,
                                       validate_step_end)

            next_steps = self.render_next_steps(request, workflow,
                                                validate_step_start,
                                                validate_step_end)
            # append rendered next steps
            data["rendered"] = next_steps

            return http.HttpResponse(json.dumps(data),
                                     content_type="application/json")

        if not workflow.is_valid():
            return self.render_to_response(context)
        try:
            success = workflow.finalize()
        except forms.ValidationError:
            return self.render_to_response(context)
        except Exception:
            success = False
            exceptions.handle(request)
        if success:
            msg = workflow.format_status_message(workflow.success_message)
            messages.success(request, msg)
        else:
            msg = workflow.format_status_message(workflow.failure_message)
            messages.error(request, msg)
        if "HTTP_X_HORIZON_ADD_TO_FIELD" in self.request.META:
            field_id = self.request.META["HTTP_X_HORIZON_ADD_TO_FIELD"]
            response = http.HttpResponse()
            if workflow.object:
                data = [self.get_object_id(workflow.object),
                        self.get_object_display(workflow.object)]
                response.content = json.dumps(data)
                response["X-Horizon-Add-To-Field"] = field_id
            return response
        next_url = self.request.POST.get(workflow.redirect_param_name)
        return shortcuts.redirect(next_url or workflow.get_success_url())

