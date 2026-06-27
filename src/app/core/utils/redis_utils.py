"""Shared Redis utilities."""

# Lua script to get and delete a key atomically
_GETDEL_LUA = """
local val = redis.call('get', KEYS[1])
if val then
    redis.call('del', KEYS[1])
end
return val
"""
