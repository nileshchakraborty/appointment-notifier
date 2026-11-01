"""Guardrails for the unattended Docker recovery timer."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


MODULE_PATH = Path(__file__).parents[1] / "management" / "docker-weekly-maintenance.py"
SPEC = importlib.util.spec_from_file_location("docker_weekly_maintenance", MODULE_PATH)
assert SPEC and SPEC.loader
maintenance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(maintenance)


def test_session_and_stateful_services_are_never_recreated_automatically():
    for host, protected in maintenance.PROTECTED_SERVICES.items():
        protected_pairs = {(project, service) for project, service, _ in protected}
        assert protected_pairs.isdisjoint(maintenance.HOST_SERVICES[host])
    assert ("openwa-fedora", "openwa", 1) in maintenance.PROTECTED_SERVICES["fedora"]
    assert ("appointment-notifier-fedora", "telegram-delivery", 3) in maintenance.PROTECTED_SERVICES["fedora"]


def test_missing_delivery_worker_stops_maintenance_before_recreation():
    def running(project, service):
        count = 2 if service == "telegram-delivery" else 1
        return [{"State": {"Status": "running", "Health": {"Status": "healthy"}}}] * count

    with patch.object(maintenance, "running_service", side_effect=running):
        with pytest.raises(RuntimeError, match="telegram-delivery"):
            maintenance.verify_protected("fedora")


def test_disconnected_openwa_stops_maintenance_even_when_docker_says_healthy():
    def running(project, service):
        count = 3 if service == "telegram-delivery" else 1
        return [{"State": {"Status": "running", "Health": {"Status": "healthy"}}}] * count

    with patch.object(maintenance, "running_service", side_effect=running), patch.object(
        maintenance, "run", return_value=SimpleNamespace(returncode=1)
    ):
        with pytest.raises(RuntimeError, match="OpenWA session is disconnected"):
            maintenance.verify_protected("fedora")
