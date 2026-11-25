# NetBox with netbox-branching, for the development stack. The plugin itself is mounted
# from the working copy, so a source change needs a restart, never a rebuild.
FROM netboxcommunity/netbox:v4.7.6

# 1.2.2 is the floor: it advances the conflict baseline on sync, which the plugin relies on.
RUN uv pip install 'netboxlabs-netbox-branching>=1.2.2,<1.3'
