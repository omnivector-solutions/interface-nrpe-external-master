#!/usr/bin/python3
"""Nrpe external master interface."""
import json
import logging
import subprocess
from datetime import datetime
from pathlib import Path

from ops.framework import (
    EventBase,
    EventSource,
    Object,
    ObjectEvents,
    StoredState,
)


logger = logging.getLogger()


class NrpeExternalMasterAvailable(EventBase):
    """Nrpe External Master Available."""


class NrpeEvents(ObjectEvents):
    """Nrpe External Master Events."""

    nrpe_external_master_available = EventSource(NrpeExternalMasterAvailable)


class Nrpe(Object):
    """Nrpe Interface."""

    _stored = StoredState()
    on = NrpeEvents()

    def __init__(self, charm, relation_name):
        """Observe relation events."""
        super().__init__(charm, relation_name)
        self._charm = charm
        self._relation_name = relation_name

        self._stored.set_default(
            nagios_check_files=set(),
            nagios_host_context=str(),
            nagios_hostname=str(),
        )

        self.framework.observe(
            self._charm.on[self._relation_name].relation_changed,
            self._on_relation_changed
        )

    def _on_relation_changed(self, event):
        relation = event.relation
        conf = self._charm.model.config
        context_from_config = conf.get('nagios_context')
        unit = self.model.unit.name.replace('/', '-')

        context = ""
        nagios_host_context = relation.__dict__.get('nagios_host_context')

        if nagios_host_context:
            context = nagios_host_context
        else:
            context = context_from_config

        self._stored.nagios_host_context = context

        host_name = ""
        nagios_hostname = relation.__dict__.get('nagios_hostname')

        if nagios_hostname:
            host_name = nagios_hostname
        else:
            host_name = f"{context}-{unit}"

        check_tmpl = """
#---------------------------------------------------
# This file is Juju managed
#---------------------------------------------------
command[%(check_name)s]=%(check_args)s
"""
        service_tmpl = """
#---------------------------------------------------
# This file is Juju managed
#---------------------------------------------------
define service {
    use                             active-service
    host_name                       %(host_name)s
    service_description             %(description)s
    check_command                   check_nrpe!%(check_name)s
    servicegroups                   %(servicegroups)s
}
"""
        slurm_component = self._charm.get_slurm_component()
        slurm_process_check_args = [
            '/usr/lib/nagios/plugins/check_procs',
            '-c', '1:', '-C', slurm_component,
        ]

        munged_process_check_args = [
            '/usr/lib/nagios/plugins/check_procs',
            '-c', '1:', '-C', "munged",
        ]

        slurm_checks = [
            {
                'check_args': slurm_process_check_args,
                'check_name': slurm_component,
                'host_name': host_name,
                'unit_name': unit,
                'description': f"Check for {slurm_component} process.",
                'context': conf.get('nagios_context') or nagios_host_context,
                'servicegroups': conf.get('nagios_servicegroups') or context,
            },
            {
                'check_args': munged_process_check_args,
                'check_name': "munged",
                'host_name': host_name,
                'unit_name': unit,
                'description': "Check for munged process.",
                'context': conf.get('nagios_context') or nagios_host_context,
                'servicegroups': conf.get('nagios_servicegroups') or context,
            },
        ]

        for check in slurm_checks:
            check_filename = \
                Path(f"/etc/nagios/nrpe.d/check_{check['check_name']}.cfg")
            check_filename.write_text(check_tmpl % check)

            service_filename = \
                Path("/var/lib/nagios/export/service__"
                     f"{check['unit_name']}_{check['check_name']}.cfg")
            service_filename.write_text(service_tmpl % check)

        iso_time = datetime.now().isoformat()
        for rel_id in _relation_ids(self._relation_name):
            relation_cmd_line = [
                'relation-set', '-r', rel_id, f'timestamp={iso_time}']
            subprocess.call(relation_cmd_line)

    def _on_relation_broken(self, event):
        files = self._nagios_files
        for f in files:
            try:
                Path(f).unlink()
            except Exception as e:
                logger.debug(f"failed to remove {f}: {e}")
        files = set()


def _relation_ids(reltype):
    """List of relation_ids."""
    relid_cmd_line = ['relation-ids', '--format=json', reltype]
    return json.loads(
        subprocess.check_output(relid_cmd_line).decode('UTF-8')) or []
