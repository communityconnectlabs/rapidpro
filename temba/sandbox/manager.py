from .config import SandboxConfig


class SandboxManager:
    def __init__(self):
        self.dummy = "ABC"

    def production_check(self):
        """
        Check if the current environment is production.
        """
        # 1. Sandbox Environment table ->  is_production (select is_production from sandbox_environment)
        # 2. Check config values
        return False

    def live_endpoint(self):
        """
        Check if the current environment is live - e.g. messages flow to production/stage endpoints and must not be spammed with fake numbers, etc.
        """

        # 1. Sandbox Environment table ->  is_production (select is_production from sandbox_environment)
        # 2. Check config values
        return False

    def get_sandbox_config(self, file: s):
        """
        Get the current sandbox configuration.
        """
        # todo load yaml file, populate sandbox config, (passing overrides) and return
        return SandboxConfig()