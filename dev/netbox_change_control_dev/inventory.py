"""
The few objects on main that the seed commands edit, created when the database has none.

A new development stack starts from an empty NetBox: without them the demo branch records
no change, no scoped policy matches, and the screenshot scenarios stop.
"""

__all__ = ('ensure_demo_inventory',)

# One device per screenshot scenario, since the conflict scenario moves its device on main.
DEVICES = 4


def ensure_demo_inventory():
    """
    Create the demo site, devices and circuit provider that are missing, and return
    the names of what was created.
    """
    from circuits.models import Provider
    from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site

    created = []
    if Device.objects.count() < DEVICES:
        site, _ = Site.objects.get_or_create(slug='demo-site', defaults={'name': 'Demo site'})
        manufacturer, _ = Manufacturer.objects.get_or_create(slug='demo', defaults={'name': 'Demo'})
        device_type, _ = DeviceType.objects.get_or_create(
            manufacturer=manufacturer, slug='demo-switch', defaults={'model': 'Demo switch'}
        )
        role, _ = DeviceRole.objects.get_or_create(slug='switch', defaults={'name': 'Switch'})
        for number in range(1, DEVICES + 1):
            device, new = Device.objects.get_or_create(
                name=f'demo-switch-{number}', site=site, defaults={'device_type': device_type, 'role': role}
            )
            if new:
                created.append(f'device {device}')
    if not Provider.objects.exists():
        created.append(f'provider {Provider.objects.create(name="Demo carrier", slug="demo-carrier")}')
    return created
