-- Decides which target override applies to a file. Pure logic, no mpv calls.

local M = {NONE = "none"}

-- 24 and 30 fps limits should still cover a container reporting 30.003.
local FPS_TOLERANCE = 0.01

function M.weight(target)
	return target.max_width * target.max_height * target.max_fps
end

--- Heaviest first; equal weights put the larger resolution first.
function M.sort(list)
	table.sort(list, function(a, b)
		local weight_a, weight_b = M.weight(a), M.weight(b)
		if weight_a ~= weight_b then return weight_a > weight_b end
		return a.max_width * a.max_height > b.max_width * b.max_height
	end)
end

--- Compares long edge to long edge, so portrait video fits its landscape limit.
function M.covers(target, width, height, fps)
	return math.max(width, height) <= math.max(target.max_width, target.max_height)
		and math.min(width, height) <= math.min(target.max_width, target.max_height)
		and fps <= target.max_fps + FPS_TOLERANCE
end

--- The lightest target covering the file, or the heaviest if none does.
function M.match(list, width, height, fps)
	local tightest, heaviest
	for _, target in ipairs(list) do
		if not heaviest or M.weight(target) > M.weight(heaviest) then heaviest = target end
		if M.covers(target, width, height, fps)
			and (not tightest or M.weight(target) < M.weight(tightest)) then
			tightest = target
		end
	end
	return tightest or heaviest
end

--- What to run for a file, or nil if it shouldn't be interpolated.
function M.resolve(settings, width, height, fps)
	if not width or not height or not fps or fps <= 0 then return nil end
	if settings.fps_cutoff > 0 and fps >= settings.fps_cutoff then return nil end
	local target = M.match(settings.targets, width, height, fps)
	local method = target and target.method or settings.default_method
	if method == M.NONE then return nil end
	return {
		method = method,
		multiplier = target and target.multiplier or settings.default_multiplier,
		target = target,
	}
end

return M
