from collections import OrderedDict
import yaml
from mcresolver.utils import is_url, filename_from_url

from bukget import BukkitResource
from spiget import SpigotResource
from importlib.util import spec_from_file_location
from jinja2 import Environment

import fnmatch
import os
import requests

# from yamlbro

__dirname, __init_python_script = os.path.split(os.path.abspath(__file__))


def get_config_from_url(url):
    return requests.get(url).text


def render_config_from_url(url, variables):
    config_data = get_config_from_url(url)
    return Environment().from_string(config_data).render(variables)


def render_config_from_string(config, variables):
    return Environment().from_string(config).render(variables)


def merge_configuration_options(config_options=None, defaults={}):
    """
    Merge all the nodes that are not present in config_options from defaults,
    to assure that the values passed to the template have an option to fill every node.

    :param config_options: Options that were passed to render the configuration by.
    :param defaults: Default values of configuration nodes, to fill in the blanks if missing in config_options
    :return: A dictionary of configuration options filled with all the missing values required to render a template.
    """

    if not config_options:
        config_options = {}

    options = config_options.copy()

    for key, value in defaults.items():
        if key not in options.keys():
            options[key] = value

    return options


def get_configuration_defaults(url=None, file=None):
    """
    Creates a dictionary of the default configuration values to provide when
    a value or option is not specified.

    Can be retrieved via a URL, or read from a file.

    :param url: URL of the page which contains the raw template defaults (think hosting a text file on a website)
    :param file: File to read the template defaults from.
    :return: Dictionary indexed by the key (template variable) and the value assigned to that template variable.
    """

    if url is None and file is None:
        raise ValueError("You must include either a url to retrieve the defaults from, or a file to read them from")

    defaults = OrderedDict()
    template_default_content = None
    if url is not None:
        template_default_content = get_config_from_url(url)
    elif file is not None:
        template_default_content = get_config_from_file(file)

    if template_default_content is None:
        raise Exception("Unable to retrieve the configuration defaults from url or file")

    defaults = yaml.load(template_default_content)
    return defaults


def configure_plugin(resource, version, parent_folder, defaults_file=None, template_file=None, config_options=None,
                     script=None, script_folder=None, **kwargs):
    resource_name = resource.plugin_name if isinstance(resource, BukkitResource) else resource.name
    # First check if we're supposed to be configuring with a script.
    if script is not None:
        # If they've passed a file to use specifically, then we're going to use that to configure the plugins.
        if os.path.isfile(script) and os.path.exists(script):
            configuration_script = __load_configuring_script(script, resource, version)
        else:
            # Otherwise Scan the scripts directory for the configuration script to handle the specified plugin.
            configuration_script = __get_configuring_script(script_folder, resource, version)

        if configuration_script is None:
            print("Unable to locate script for %s" % resource_name)
            return False
        else:
            print("Found script for %s" % resource_name)

        configure_method = getattr(configuration_script, 'configure')
        if configure_method is None:
            raise AttributeError("Unable to find 'configure' method in configuration script")

        configure_method(parent_folder, config_options=config_options, **kwargs)
        return True

    if defaults_file is None or template_file is None:
        print("Unable to configure %s without a script, or templates & default folder" % resource_name)
        return False

    plugin_folder = os.path.expanduser(os.path.join(parent_folder, kwargs.get('plugin_folder', resource_name)))

    if not os.path.exists(plugin_folder):
        os.makedirs(plugin_folder)

    # Get the default configuration values for Commons, incase some aren't present in the options.
    try:
        if is_url(defaults_file):
            defaults = get_configuration_defaults(url=defaults_file)
        else:
            if not os.path.exists(defaults_file):
                print("Unable to locate default variables file for %s %s" % (resource_name, defaults_file))
                return False

            defaults = get_configuration_defaults(file=defaults_file)
    except:
        print("Unable to locate default variables file for %s %s" % (resource_name, defaults_file))
        return False

    # Create a full dictionary of all the options required to render the template, merging
    # in the missing values from the default config.
    options = merge_configuration_options(config_options, defaults)

    # todo implement config file name in options.
    config_file = os.path.join(plugin_folder, 'config.yml')

    # Render the configuration of the url, with the options (and defaults included)
    if is_url(template_file):
        plugin_config = render_config_from_url(template_file, options)
    else:
        if not os.path.exists(template_file):
            print("Unable to locate template file for %s %s" % (resource_name, defaults_file))
            return False
        plugin_config = render_config_from_string(get_config_from_file(template_file), options)


    # Lastly write the configuration to the file specified!
    write_file(config_file, plugin_config)
    print("Configuration for {plugin} ({version}) has been rendered!".format(resource_name, version))
    return True


def get_config_from_file(file, trim_newlines=True):
    with open(file, 'r') as config_file:
        data = config_file.read()
        if trim_newlines:
            data = data.replace('\n', '')
        return data


def write_file(file, data):
    with open(file, 'w') as data_file:
        data_file.write(data)


def save_plugin_config_script(script_folder, script_url):
    import requests

    if not os.path.exists(script_folder):
        os.makedirs(script_folder)

    script_name = filename_from_url(script_url)
    script_data = requests.get(script_url).text

    script_loc = os.path.join(script_folder, script_name)

    write_file(script_loc, script_data)

    if not os.path.exists(script_loc):
        raise FileNotFoundError("Unable to locate file %s after attempting to save it" % script_loc)

    return script_loc


def __get_plugin_identifier(resource):
    plugin_identifier = None

    if isinstance(resource, BukkitResource):
        plugin_identifier = resource.plugin_name
    elif isinstance(resource, SpigotResource):
        plugin_identifier = resource.resource_id
    else:
        return None

    return plugin_identifier


def __load_configuring_script(script_location, resource, version):
    """
    Load a configuration script from a location on disk, and verify it's for the resource and version desired.
    :param script_location: Location of the script (on disk) to check.
    :param resource: Resource to verify the configuration script by;
    We don't want to run a script that's supposed to configure another resource
    :param version: Version of the resource to verify the script is supposed to configure.
    :return: the Module loaded via the script if it's valid, otherwise none.
    """
    valid_script, config_module = __is_valid_configuration_script(script_location, resource, version)
    if not valid_script:
        return None

    return config_module


def __is_valid_configuration_script(script_location, resource, version):
    """
    Check whether or not a script is valid for the desired resource, and version.
    :param script_location: Location (on disk) of where the script is located.
    :param resource: Resource to check if the script is valid for.
    :param version: version of the resource to check if the script is valid.
    :return: True if the specified script is valid to configure the desired resource and version, false otherwise.
    """
    plugin_identifier = str(__get_plugin_identifier(resource))

    if "__init__" in script_location:
        return False, None

    config_module = __import_module_from_file(os.path.expanduser(script_location))

    if config_module is None:
        return False, None

    if not hasattr(config_module, "configure"):
        return False, None

    if not hasattr(config_module, '_plugin_id_'):
        return False, None

    if not hasattr(config_module, '_plugin_versions_'):
        return False, None

    config_plugin_id = getattr(config_module, "_plugin_id_")
    config_plugin_versions = getattr(config_module, "_plugin_versions_")

    if config_plugin_id is None or config_plugin_versions is None:
        return False, None

    if plugin_identifier.lower() != config_plugin_id.lower():
        return False, None

    # Version checking in a list, that way a config script can support multiple versions
    found_version = False

    if "all" in config_plugin_versions:
        found_version = True

    if found_version is False:
        for usable_version in config_plugin_versions:
            if version.lower() in usable_version.lower():
                found_version = True
                break

    if found_version:
        return True, config_module
    else:
        return False, None


def __get_configuring_script(scripts_folder, resource, version):
    config_script_names = __get_files_recursive(scripts_folder, "*.py")

    for config_script in config_script_names:
        config_module = __load_configuring_script(config_script, resource, version)
        if config_module is not None:
            return config_module

    return None


def __get_files_recursive(path, match='*.py'):
    matches = []
    for root, dirnames, filenames in os.walk(path):
        for filename in fnmatch.filter(filenames, match):
            matches.append(os.path.join(root, filename))
        break
    return matches


def __import_module_from_file(full_path_to_module):
    """
    Import a module given the full path/filename of the .py file
    Python 3.4
    """

    module = None

    try:

        # Get module name and path from full path
        module_dir, module_file = os.path.split(full_path_to_module)
        module_name, module_ext = os.path.splitext(module_file)

        # Get module "spec" from filename
        spec = spec_from_file_location(module_name, full_path_to_module)

        module = spec.loader.load_module()

    except Exception as ec:
        # Simple error printing
        # Insert "sophisticated" stuff here
        print(ec)

    finally:
        return module
