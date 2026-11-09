-- Loaded into a headless mpv by the benchmark. Reports how fast video went through.

local mp = require("mp")

-- The test clip is 24 fps and the benchmark doubles it.
local OUTPUT_FPS = 48
-- Seconds of video to skip, so filter start-up isn't counted.
local WARMUP = 1

local started_at, started_position, last_at, last_position

mp.observe_property("time-pos", "number", function(_, position)
	if not position or position < WARMUP then return end
	local now = mp.get_time()
	if not started_at then started_at, started_position = now, position end
	last_at, last_position = now, position
end)

mp.register_event("end-file", function()
	if not started_at or last_at <= started_at then return end
	print(string.format(
		"MPVE_BENCH frames=%.0f seconds=%.3f",
		(last_position - started_position) * OUTPUT_FPS,
		last_at - started_at
	))
end)
