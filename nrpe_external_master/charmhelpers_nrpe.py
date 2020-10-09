#!/usr/bin/python3
"""Compatibility with the nrpe-external-master charm"""
# Charmhelpers functions
# Copyright 2014-2015 Canonical Limited.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# NRPE
# Copyright 2012 Canonical Ltd.
#
# Authors:
#  Matthew Wedgwood <matthew.wedgwood@canonical.com>
#
# This module adds compatibility with the nrpe-external-master and plain nrpe
# subordinate charms. To use it in your charm:
#
# 1. Update metadata.yaml
#
#   provides:
#     (...)
#     nrpe-external-master:
#       interface: nrpe-external-master
#       scope: container
#
#   and/or
#
#   provides:
#     (...)
#     local-monitors:
#       interface: local-monitors
#       scope: container

#
# 2. Add the following to config.yaml
#
#    nagios_context:
#      default: "juju"
#      type: string
#      description: |
#        Used by the nrpe subordinate charms.
#        A string that will be prepended to instance name to set the host name
#        in nagios. So for instance the hostname would be something like:
#            juju-myservice-0
#        If you're running multiple environments with the same services in them
#        this allows you to differentiate between them.
#    nagios_servicegroups:
#      default: ""
#      type: string
#      description: |
#        A comma-separated list of nagios servicegroups.
#        If left empty, the nagios_context will be used as the servicegroup
#
# 3. Add custom checks (Nagios plugins) to files/nrpe-external-master
#
# 4. Update your hooks.py with something like this:
#
#    from charmsupport.nrpe import NRPE
#    (...)
#    def update_nrpe_config():
#        nrpe_compat = NRPE()
#        nrpe_compat.add_check(
#            shortname = "myservice",
#            description = "Check MyService",
#            check_cmd = "check_http -w 2 -c 10 http://localhost"
#            )
#        nrpe_compat.add_check(
#            "myservice_other",
#            "Check for widget failures",
#            check_cmd = "/srv/myapp/scripts/widget_check"
#            )
#        nrpe_compat.write()
#
#    def config_changed():
#        (...)
#        update_nrpe_config()
#
#    def nrpe_external_master_relation_changed():
#        update_nrpe_config()
#
#    def local_monitors_relation_changed():
#        update_nrpe_config()
#
# 4.a If your charm is a subordinate charm set primary=False
#
#    from charmsupport.nrpe import NRPE
#    (...)
#    def update_nrpe_config():
#        nrpe_compat = NRPE(primary=False)
#
# 5. ln -s hooks.py nrpe-external-master-relation-changed
#    ln -s hooks.py local-monitors-relation-changed

import copy
import grp
import json
import logging
import os
import pwd
import re
import shlex
import subprocess
import sys
import tempfile
from functools import wraps

import yaml


logger = logging.getLogger()


cache = {}

_atexit = []


def atexit(callback, *args, **kwargs):
    '''Schedule a callback to run on successful hook completion.
    Callbacks are run in the reverse order that they were added.'''
    _atexit.append((callback, args, kwargs))


def charm_dir():
    """Return the root directory of the current charm"""
    d = os.environ.get('JUJU_CHARM_DIR')
    if d is not None:
        return d
    return os.environ.get('CHARM_DIR')


def cached(func):
    """Cache return values for multiple executions of func + args
    For example::
        @cached
        def unit_get(attribute):
            pass
        unit_get('test')
    will cache the result of unit_get + 'test' for future calls.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        global cache
        key = json.dumps((func, args, kwargs), sort_keys=True, default=str)
        try:
            return cache[key]
        except KeyError:
            pass  # Drop out of the exception handler scope.
        res = func(*args, **kwargs)
        cache[key] = res
        return res
    wrapper._wrapped = func
    return wrapper


def flush(key):
    """Flushes any entries from function cache where the
    key is found in the function+args """
    flush_list = []
    for item in cache:
        if key in item:
            flush_list.append(item)
    for item in flush_list:
        del cache[item]


@cached
def relation_for_unit(unit=None, rid=None):
    """Get the json represenation of a unit's relation"""
    unit = unit or remote_unit()
    relation = relation_get(unit=unit, rid=rid)
    for key in relation:
        if key.endswith('-list'):
            relation[key] = relation[key].split()
    relation['__unit__'] = unit
    return relation


@cached
def relations_for_id(relid=None):
    """Get relations of a specific relation ID"""
    relation_data = []
    relid = relid or relation_ids()
    for unit in related_units(relid):
        unit_data = relation_for_unit(unit, relid)
        unit_data['__relid__'] = relid
        relation_data.append(unit_data)
    return relation_data


@cached
def relations_of_type(reltype=None):
    """Get relations of a specific type"""
    relation_data = []
    reltype = reltype or relation_type()
    for relid in relation_ids(reltype):
        for relation in relations_for_id(relid):
            relation['__relid__'] = relid
            relation_data.append(relation)
    return relation_data


@cached
def relation_ids(reltype=None):
    """A list of relation_ids"""
    reltype = reltype or relation_type()
    relid_cmd_line = ['relation-ids', '--format=json']
    if reltype is not None:
        relid_cmd_line.append(reltype)
        return json.loads(
            subprocess.check_output(relid_cmd_line).decode('UTF-8')) or []
    return []


@cached
def relation_id(relation_name=None, service_or_unit=None):
    """The relation ID for the current or a specified relation"""
    if not relation_name and not service_or_unit:
        return os.environ.get('JUJU_RELATION_ID', None)
    elif relation_name and service_or_unit:
        service_name = service_or_unit.split('/')[0]
        for relid in relation_ids(relation_name):
            remote_service = remote_service_name(relid)
            if remote_service == service_name:
                return relid
    else:
        raise ValueError('Must specify neither or both of relation_name and service_or_unit')


def relation_type():
    """The scope for the current relation hook"""
    return os.environ.get('JUJU_RELATION', None)


@cached
def remote_service_name(relid=None):
    """The remote service name for a given relation-id (or the current relation)"""
    if relid is None:
        unit = remote_unit()
    else:
        units = related_units(relid)
        unit = units[0] if units else None
    return unit.split('/')[0] if unit else None


def remote_unit():
    """The remote unit for the current relation hook"""
    return os.environ.get('JUJU_REMOTE_UNIT', None)


@cached
def related_units(relid=None):
    """A list of related units"""
    relid = relid or relation_id()
    units_cmd_line = ['relation-list', '--format=json']
    if relid is not None:
        units_cmd_line.extend(('-r', relid))
    return json.loads(
        subprocess.check_output(units_cmd_line).decode('UTF-8')) or []


def relation_set(relation_id=None, relation_settings=None, **kwargs):
    """Set relation information for the current unit"""
    relation_settings = relation_settings if relation_settings else {}
    relation_cmd_line = ['relation-set']
    accepts_file = "--file" in subprocess.check_output(
        relation_cmd_line + ["--help"], universal_newlines=True)
    if relation_id is not None:
        relation_cmd_line.extend(('-r', relation_id))
    settings = relation_settings.copy()
    settings.update(kwargs)
    for key, value in settings.items():
        # Force value to be a string: it always should, but some call
        # sites pass in things like dicts or numbers.
        if value is not None:
            settings[key] = "{}".format(value)
    if accepts_file:
        # --file was introduced in Juju 1.23.2. Use it by default if
        # available, since otherwise we'll break if the relation data is
        # too big. Ideally we should tell relation-set to read the data from
        # stdin, but that feature is broken in 1.23.2: Bug #1454678.
        with tempfile.NamedTemporaryFile(delete=False) as settings_file:
            settings_file.write(yaml.safe_dump(settings).encode("utf-8"))
        subprocess.check_call(
            relation_cmd_line + ["--file", settings_file.name])
        os.remove(settings_file.name)
    else:
        for key, value in settings.items():
            if value is None:
                relation_cmd_line.append('{}='.format(key))
            else:
                relation_cmd_line.append('{}={}'.format(key, value))
        subprocess.check_call(relation_cmd_line)
    # Flush cache of any relation-gets for local unit
    flush(local_unit())


@cached
def relation_get(attribute=None, unit=None, rid=None):
    """Get relation information"""
    _args = ['relation-get', '--format=json']
    if rid:
        _args.append('-r')
        _args.append(rid)
    _args.append(attribute or '-')
    if unit:
        _args.append(unit)
    try:
        return json.loads(subprocess.check_output(_args).decode('UTF-8'))
    except ValueError:
        return None
    except subprocess.CalledProcessError as e:
        if e.returncode == 2:
            return None
        raise


def local_unit():
    """Local unit ID"""
    return os.environ['JUJU_UNIT_NAME']


def hook_name():
    """The name of the currently executing hook"""
    return os.environ.get('JUJU_HOOK_NAME', os.path.basename(sys.argv[0]))


class Config(dict):
    """A dictionary representation of the charm's config.yaml, with some
    extra features:
    - See which values in the dictionary have changed since the previous hook.
    - For values that have changed, see what the previous value was.
    - Store arbitrary data for use in a later hook.
    """
    CONFIG_FILE_NAME = '.juju-persistent-config'

    def __init__(self, *args, **kw):
        super(Config, self).__init__(*args, **kw)
        self.implicit_save = True
        self._prev_dict = None
        self.path = os.path.join(charm_dir(), Config.CONFIG_FILE_NAME)
        if os.path.exists(self.path) and os.stat(self.path).st_size:
            self.load_previous()
        atexit(self._implicit_save)

    def load_previous(self, path=None):
        """Load previous copy of config from disk.
        In normal usage you don't need to call this method directly - it
        is called automatically at object initialization.
        :param path:
            File path from which to load the previous config. If `None`,
            config is loaded from the default location. If `path` is
            specified, subsequent `save()` calls will write to the same
            path.
        """
        self.path = path or self.path
        with open(self.path) as f:
            try:
                self._prev_dict = json.load(f)
            except ValueError as e:
                logger.error(
                    'Found but was unable to parse previous config data, '
                    f'ignoring which will report all values as changed - {e}'
                )
                return
        for k, v in copy.deepcopy(self._prev_dict).items():
            if k not in self:
                self[k] = v

    def changed(self, key):
        """Return True if the current value for this key is different from
        the previous value.
        """
        if self._prev_dict is None:
            return True
        return self.previous(key) != self.get(key)

    def previous(self, key):
        """Return previous value for this key, or None if there
        is no previous value.
        """
        if self._prev_dict:
            return self._prev_dict.get(key)
        return None

    def save(self):
        """Save this config to disk.
        If the charm is using the :mod:`Services Framework <services.base>`
        or :meth:'@hook <Hooks.hook>' decorator, this
        is called automatically at the end of successful hook execution.
        Otherwise, it should be called directly by user code.
        To disable automatic saves, set ``implicit_save=False`` on this
        instance.
        """
        with open(self.path, 'w') as f:
            os.fchmod(f.fileno(), 0o600)
            json.dump(self, f)

    def _implicit_save(self):
        if self.implicit_save:
            self.save()

def config():
    config_data = ""
    config_cmd_line = ['config-get', '--all', '--format=json']
    try:
        config_data = json.loads(
            subprocess.check_output(config_cmd_line).decode('UTF-8')
        )
    except json.decoder.JSONDecodeError as e:
        logger.error(
            'Unable to parse output from config-get: '
            f'config_cmd_line="{config_cmd_line} message="{e}"'
        )
        return None
    return Config(config_data)


def service(action, service_name):
    """Control a system service.
    :param action: the action to take on the service
    :param service_name: the name of the service to perform th action on
    :param **kwargs: additional params to be passed to the service command in
                    the form of key=value.
    """
    cmd = ['systemctl', action, service_name]
    return subprocess.call(cmd) == 0



class CheckException(Exception):
    pass


class Check(object):
    shortname_re = '[A-Za-z0-9-_.@]+$'
    service_template = ("""
#---------------------------------------------------
# This file is Juju managed
#---------------------------------------------------
define service {{
    use                             active-service
    host_name                       {nagios_hostname}
    service_description             {nagios_hostname}[{shortname}] """
                        """{description}
    check_command                   check_nrpe!{command}
    servicegroups                   {nagios_servicegroup}
}}
""")

    def __init__(self, shortname, description, check_cmd):
        super(Check, self).__init__()
        # XXX: could be better to calculate this from the service name
        if not re.match(self.shortname_re, shortname):
            raise CheckException("shortname must match {}".format(
                Check.shortname_re))
        self.shortname = shortname
        self.command = "check_{}".format(shortname)
        # Note: a set of invalid characters is defined by the
        # Nagios server config
        # The default is: illegal_object_name_chars=`~!$%^&*"|'<>?,()=
        self.description = description
        self.check_cmd = self._locate_cmd(check_cmd)

    def _get_check_filename(self):
        return os.path.join(NRPE.nrpe_confdir, f'{self.command}.cfg')

    def _get_service_filename(self, hostname):
        return os.path.join(NRPE.nagios_exportdir,
                            'service__{hostname}_{self.command}.cfg')

    def _locate_cmd(self, check_cmd):
        search_path = (
            '/usr/lib/nagios/plugins',
            '/usr/local/lib/nagios/plugins',
        )
        parts = shlex.split(check_cmd)
        for path in search_path:
            if os.path.exists(os.path.join(path, parts[0])):
                command = os.path.join(path, parts[0])
                if len(parts) > 1:
                    command += " " + " ".join(parts[1:])
                return command
        logger.debug('Check command not found: {}'.format(parts[0]))
        return ''

    def _remove_service_files(self):
        if not os.path.exists(NRPE.nagios_exportdir):
            return
        for f in os.listdir(NRPE.nagios_exportdir):
            if f.endswith(f'_{self.command}.cfg'):
                os.remove(os.path.join(NRPE.nagios_exportdir, f))

    def remove(self, hostname):
        nrpe_check_file = self._get_check_filename()
        if os.path.exists(nrpe_check_file):
            os.remove(nrpe_check_file)
        self._remove_service_files()

    def write(self, nagios_context, hostname, nagios_servicegroups):
        nrpe_check_file = self._get_check_filename()
        with open(nrpe_check_file, 'w') as nrpe_check_config:
            nrpe_check_config.write("# check {}\n".format(self.shortname))
            if nagios_servicegroups:
                nrpe_check_config.write(
                    "# The following header was added automatically by juju\n")
                nrpe_check_config.write(
                    "# Modifying it will affect nagios monitoring and alerting\n")
                nrpe_check_config.write(
                    "# servicegroups: {}\n".format(nagios_servicegroups))
            nrpe_check_config.write("command[{}]={}\n".format(
                self.command, self.check_cmd))

        if not os.path.exists(NRPE.nagios_exportdir):
            logger.debug(
                'Not writing service config as {} is not accessible'.format(
                    NRPE.nagios_exportdir
                )
            )
        else:
            self.write_service_config(nagios_context, hostname,
                                      nagios_servicegroups)

    def write_service_config(self, nagios_context, hostname,
                             nagios_servicegroups):
        self._remove_service_files()

        templ_vars = {
            'nagios_hostname': hostname,
            'nagios_servicegroup': nagios_servicegroups,
            'description': self.description,
            'shortname': self.shortname,
            'command': self.command,
        }
        nrpe_service_text = Check.service_template.format(**templ_vars)
        nrpe_service_file = self._get_service_filename(hostname)
        with open(nrpe_service_file, 'w') as nrpe_service_config:
            nrpe_service_config.write(str(nrpe_service_text))

    def run(self):
        subprocess.call(self.check_cmd)


class NRPE(object):
    nagios_logdir = '/var/log/nagios'
    nagios_exportdir = '/var/lib/nagios/export'
    nrpe_confdir = '/etc/nagios/nrpe.d'
    homedir = '/var/lib/nagios'  # home dir provided by nagios-nrpe-server

    def __init__(self, hostname=None, primary=True):
        super(NRPE, self).__init__()
        self.config = config()
        self.primary = primary
        self.nagios_context = self.config['nagios_context']
        if 'nagios_servicegroups' in self.config and self.config['nagios_servicegroups']:
            self.nagios_servicegroups = self.config['nagios_servicegroups']
        else:
            self.nagios_servicegroups = self.nagios_context
        self.unit_name = local_unit().replace('/', '-')
        if hostname:
            self.hostname = hostname
        else:
            nagios_hostname = get_nagios_hostname()
            if nagios_hostname:
                self.hostname = nagios_hostname
            else:
                self.hostname = f"{self.nagios_context}-{self.unit_name}"
        self.checks = []
        # Iff in an nrpe-external-master relation hook, set primary status
        relation = relation_ids('nrpe-external-master')
        if relation:
            logger.debug(f"Setting charm primary status {primary}.")
            for rid in relation:
                relation_set(relation_id=rid, relation_settings={'primary': self.primary})
        self.remove_check_queue = set()

    @classmethod
    def does_nrpe_conf_dir_exist(cls):
        """Return True if th nrpe_confdif directory exists."""
        return os.path.isdir(cls.nrpe_confdir)

    def add_check(self, *args, **kwargs):
        shortname = None
        if kwargs.get('shortname') is None:
            if len(args) > 0:
                shortname = args[0]
        else:
            shortname = kwargs['shortname']

        self.checks.append(Check(*args, **kwargs))
        try:
            self.remove_check_queue.remove(shortname)
        except KeyError:
            pass

    def remove_check(self, *args, **kwargs):
        if kwargs.get('shortname') is None:
            raise ValueError('shortname of check must be specified')

        # Use sensible defaults if they're not specified - these are not
        # actually used during removal, but they're required for constructing
        # the Check object; check_disk is chosen because it's part of the
        # nagios-plugins-basic package.
        if kwargs.get('check_cmd') is None:
            kwargs['check_cmd'] = 'check_disk'
        if kwargs.get('description') is None:
            kwargs['description'] = ''

        check = Check(*args, **kwargs)
        check.remove(self.hostname)
        self.remove_check_queue.add(kwargs['shortname'])

    def write(self):
        try:
            nagios_uid = pwd.getpwnam('nagios').pw_uid
            nagios_gid = grp.getgrnam('nagios').gr_gid
        except Exception:
            logger.debug("Nagios user not set up, nrpe checks not updated")
            return

        if not os.path.exists(NRPE.nagios_logdir):
            os.mkdir(NRPE.nagios_logdir)
            os.chown(NRPE.nagios_logdir, nagios_uid, nagios_gid)

        nrpe_monitors = {}
        monitors = {"monitors": {"remote": {"nrpe": nrpe_monitors}}}

        # check that the charm can write to the conf dir.  If not, then nagios
        # probably isn't installed, and we can defer.
        if not self.does_nrpe_conf_dir_exist():
            return

        for nrpecheck in self.checks:
            nrpecheck.write(self.nagios_context, self.hostname,
                            self.nagios_servicegroups)
            nrpe_monitors[nrpecheck.shortname] = {
                "command": nrpecheck.command,
            }

        # update-status hooks are configured to firing every 5 minutes by
        # default. When nagios-nrpe-server is restarted, the nagios server
        # reports checks failing causing unnecessary alerts. Let's not restart
        # on update-status hooks.
        if not hook_name() == 'update-status':
            service('restart', 'nagios-nrpe-server')

        monitor_ids = relation_ids("local-monitors") + \
            relation_ids("nrpe-external-master")
        for rid in monitor_ids:
            reldata = relation_get(unit=local_unit(), rid=rid)
            if 'monitors' in reldata:
                # update the existing set of monitors with the new data
                old_monitors = yaml.safe_load(reldata['monitors'])
                old_nrpe_monitors = old_monitors['monitors']['remote']['nrpe']
                # remove keys that are in the remove_check_queue
                old_nrpe_monitors = {k: v for k, v in old_nrpe_monitors.items()
                                     if k not in self.remove_check_queue}
                # update/add nrpe_monitors
                old_nrpe_monitors.update(nrpe_monitors)
                old_monitors['monitors']['remote']['nrpe'] = old_nrpe_monitors
                # write back to the relation
                relation_set(relation_id=rid, monitors=yaml.dump(old_monitors))
            else:
                # write a brand new set of monitors, as no existing ones.
                relation_set(relation_id=rid, monitors=yaml.dump(monitors))

        self.remove_check_queue.clear()


def get_nagios_hostcontext(relation_name='nrpe-external-master'):
    """
    Query relation with nrpe subordinate, return the nagios_host_context
    :param str relation_name: Name of relation nrpe sub joined to
    """
    for rel in relations_of_type(relation_name):
        if 'nagios_host_context' in rel:
            return rel['nagios_host_context']


def get_nagios_hostname(relation_name='nrpe-external-master'):
    """
    Query relation with nrpe subordinate, return the nagios_hostname
    :param str relation_name: Name of relation nrpe sub joined to
    """
    for rel in relations_of_type(relation_name):
        if 'nagios_hostname' in rel:
            return rel['nagios_hostname']


def get_nagios_unit_name(relation_name='nrpe-external-master'):
    """
    Return the nagios unit name prepended with host_context if needed
    :param str relation_name: Name of relation nrpe sub joined to
    """
    host_context = get_nagios_hostcontext(relation_name)
    if host_context:
        unit = f"{host_context}:{local_unit()}"
    else:
        unit = local_unit()
    return unit
