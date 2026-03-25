-- Atomically XADD a message and PUBLISH a notification
-- KEYS[1] = stream key, KEYS[2] = notify channel
-- ARGV[1] = field key, ARGV[2] = message data, ARGV[3] = maxlen, ARGV[4] = notification payload
local id = redis.call('XADD', KEYS[1], 'MAXLEN', '~', ARGV[3], '*', ARGV[1], ARGV[2])
redis.call('PUBLISH', KEYS[2], ARGV[4])
return id
