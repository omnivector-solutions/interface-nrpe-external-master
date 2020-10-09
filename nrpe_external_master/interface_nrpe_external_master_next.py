#!/usr/bin/python3
"""Nrpe external master interface."""
import datetime
import logging
from pathlib import Path

from charmhelpers_nrpe import NRPE as CH_NRPE
from ops.framework import Object


logger = logging.getLogger()


class Nrpe(Object):
    """Nrpe Interface."""

    def __init__(self, charm, relation_name):
        """Observe relation events."""
        super().__init__(charm, relation_name)
        self._charm = charm
        self._relation_name = relation_name

        self.framework.observe(
            self._charm.on[self._relation_name].relation_changed,
            self._on_relation_changed
        )

    def _on_relation_changed(self, event):
        nrpe_compat = CH_NRPE()
        slurm_component = self._charm.get_slurm_component()

        slurm_process_check_args = [
            '/usr/lib/nagios/plugins/check_procs',
            '-c', '1:', '-C', slurm_component,
        ]
        munged_process_check_args = [
            '/usr/lib/nagios/plugins/check_procs',
            '-c', '1:', '-C', "munged",
        ]

        nrpe_compat.add_check(
            shortname = slurm_component,
            description = f"Process check for {slurm_component}",
            check_cmd = " ".join(slurm_process_check_args)
        )
        nrpe_compat.add_check(
            shortname = "munged",
            description = f"Process check for munged",
            check_cmd = " ".join(munged_process_check_args)
        )
        nrpe_compat.write()
