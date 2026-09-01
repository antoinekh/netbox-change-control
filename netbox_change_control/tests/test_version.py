"""
The version is written twice, so the two copies have to agree.

`pyproject.toml` is what PyPI publishes and `__version__` is what the plugin reports to
NetBox, and both are cut by hand at release. 0.4.0 shipped with them disagreeing: the package
metadata said 0.4.0 while the plugins list in NetBox said 0.3.0, so the version an operator
can see was not the version they had installed.
"""

import tomllib
from pathlib import Path

from django.test import SimpleTestCase

from netbox_change_control import ChangeControlConfig, __version__

# Beside the package rather than inside it, like the documentation, so this reads it from a
# checkout only. CI installs the plugin editable, which is why that works there.
PYPROJECT = Path(__file__).resolve().parent.parent.parent / 'pyproject.toml'


class VersionTest(SimpleTestCase):
    def test_version_matches_pyproject(self):
        if not PYPROJECT.exists():
            self.skipTest(f'{PYPROJECT} is missing, so this is not a checkout')

        packaged = tomllib.loads(PYPROJECT.read_text())['project']['version']
        self.assertEqual(
            __version__,
            packaged,
            'pyproject.toml and __version__ disagree, so the plugin reports a version which is '
            'not the one that was packaged. Both are bumped by hand at release.',
        )

    def test_the_plugin_reports_that_version(self):
        """
        NetBox reads the version off the config, which is what the plugins list shows.
        """
        self.assertEqual(ChangeControlConfig.version, __version__)
