"""
The configuration table must list every setting, with the default the plugin actually uses.

A setting added in code and not written down is a setting nobody knows to set, and a default
that has moved since somebody wrote the table is worse than no table: it is read as a promise.
This compares the page against `default_settings`, the same way the permissions page is
compared against the models.
"""

import re

from django.test import TestCase

from netbox_change_control import ChangeControlConfig
from netbox_change_control.tests.base import docs_page

PAGE = 'installation.md'

# A row of the configuration table: the setting, its default, and what it means.
ROW = re.compile(r'^\| `([a-z_]+)` \| `(.+?)` \| .+ \|$', re.M)


def documented_settings():
    return {name: default for name, default in ROW.findall(docs_page(PAGE))}


class DocumentedSettingsTest(TestCase):
    def test_every_setting_is_documented(self):
        missing = sorted(set(ChangeControlConfig.default_settings) - set(documented_settings()))
        self.assertEqual(missing, [], f'{PAGE} does not list these settings, which the plugin defines')

    def test_no_documented_setting_is_invented(self):
        invented = sorted(set(documented_settings()) - set(ChangeControlConfig.default_settings))
        self.assertEqual(invented, [], f'{PAGE} lists these settings, which the plugin does not define')

    def test_every_documented_default_is_the_real_one(self):
        """
        Written as Python, because that is what a reader copies into PLUGINS_CONFIG.
        """
        documented = documented_settings()
        wrong = {
            name: (documented[name], repr(default))
            for name, default in ChangeControlConfig.default_settings.items()
            if name in documented and documented[name] != repr(default)
        }
        self.assertEqual(wrong, {}, f'{PAGE} gives a default which is not the one in the code')
