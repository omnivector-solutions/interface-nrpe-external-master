#!/usr/bin/python3
"""Nrpe external master interface."""
import datetime
import logging
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
        context_from_config = self._charm.model.config['nagios_context']
        unit = self.model.unit.name.replace('/', '-')

        context = ""
        nagios_host_context = relation.__dict__.get('nagios_host_context')

        if nagios_host_context:
            context = nagios_host_context
        else:
            context = context_from_config

        self._stored.nagios_host_context = context

        hostname = ""
        nagios_hostname = relation.__dict__.get('nagios_hostname')

        if nagios_hostname:
            hostname = nagios_hostname
        else:
            hostname = f"{context}-{unit}"

        self._stored.nagios_hostname = hostname
        self.on.nrpe_external_master_available.emit()

    def _on_relation_broken(self, event):
        files = self._nagios_files
        for f in files:
            try:
                Path(f).unlink()
            except Exception as e:
                logger.debug(f"failed to remove {f}: {e}")
        files = set()

    @property
    def _relation(self):
        return self.framework.model.get_relation(self._relation_name)

    @property
    def _nagios_files(self):
        return self._stored.nagios_check_files

    def add_check(self,
                  args,
                  name,
                  description,
                  context,
                  servicegroups,
                  unit) -> None:
        """Add nagios check."""
        relation = self._relation
        unit = self.model.unit.name.replace('/', '-')
        context_from_config = self._charm.model.config['nagios_context']

        context = self._stored.nagios_host_context
        host_name = self._stored.nagios_hostname

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
        check_filename = Path(f"/etc/nagios/nrpe.d/check_{name}.cfg")
        check_filename.write_text(check_tmpl % {
            'check_args': ' '.join(args),
            'check_name': name,
        })
        self._nagios_files.add(str(check_filename))

        service_filename = Path(f"/var/lib/nagios/export/service__{unit}_{name}.cfg")
        service_filename.write_text(service_tmpl % {
            'servicegroups': servicegroups or context,
            'context': context,
            'description': description,
            'check_name': name,
            'host_name': host_name,
            'unit_name': unit,
        })
        self._nagios_files.add(str(service_filename))

        relation.data[self.model.unit]['timestamp'] = datetime.datetime.now().isoformat()
