-- Validates settings and applies menu edits to them. Pure logic, no mpv calls.

local targets = require("targets")

local M = {}

local MALFORMED = "MPV Enhanced: some saved settings were invalid and were reset"
local DUPLICATE = "A target with those limits already exists"
local ALL_USED = "Every combination of resolution and frame rate already has a target"
local SETTING_KEYS = {"default_method", "default_multiplier", "fps_cutoff"}
local TARGET_KEYS = {"max_width", "max_height", "max_fps", "method", "multiplier"}

local function is_known_method(info, id)
	if id == targets.NONE then return true end
	for _, choice in ipairs(info.choices) do
		if choice.id == id then return true end
	end
	return false
end

local function is_positive(value)
	return type(value) == "number" and value > 0
end

local function is_valid_target(value, info)
	return type(value) == "table"
		and is_positive(value.max_width)
		and is_positive(value.max_height)
		and is_positive(value.max_fps)
		and is_positive(value.multiplier)
		and is_known_method(info, value.method)
end

local function has_limits(settings, width, height, fps, except_id)
	for _, target in ipairs(settings.targets) do
		if target.id ~= except_id
			and target.max_width == width
			and target.max_height == height
			and target.max_fps == fps then
			return true
		end
	end
	return false
end

local function add_target(settings, target)
	local copy = {id = "t" .. settings.next_id}
	for _, key in ipairs(TARGET_KEYS) do copy[key] = target[key] end
	settings.next_id = settings.next_id + 1
	settings.targets[#settings.targets + 1] = copy
end

--- Settings from a parsed file, plus a warning if anything in it had to be dropped.
function M.normalize(raw, info)
	local options = info.options or {multipliers = {2}, default_fps_cutoff = 60}
	local first = info.choices[1]
	local settings = {
		enabled = false,
		default_method = first and first.id or targets.NONE,
		default_multiplier = options.multipliers[1],
		fps_cutoff = options.default_fps_cutoff,
		targets = {},
		next_id = 1,
	}
	if raw == nil then return settings, nil end
	if type(raw) ~= "table" then return settings, MALFORMED end

	local malformed = false
	local function take(key, is_valid)
		if raw[key] == nil then return end
		if is_valid(raw[key]) then settings[key] = raw[key] else malformed = true end
	end
	take("enabled", function(value) return type(value) == "boolean" end)
	take("default_method", function(value) return is_known_method(info, value) end)
	take("default_multiplier", is_positive)
	take("fps_cutoff", function(value) return type(value) == "number" and value >= 0 end)

	if type(raw.targets) == "table" then
		for _, value in ipairs(raw.targets) do
			if is_valid_target(value, info)
				and not has_limits(settings, value.max_width, value.max_height, value.max_fps) then
				add_target(settings, value)
			else
				malformed = true
			end
		end
	elseif raw.targets ~= nil then
		malformed = true
	end
	targets.sort(settings.targets)
	return settings, malformed and MALFORMED or nil
end

function M.copy(settings)
	local result = {}
	for key, value in pairs(settings) do result[key] = value end
	result.targets = {}
	for index, target in ipairs(settings.targets) do
		local copy = {}
		for key, value in pairs(target) do copy[key] = value end
		result.targets[index] = copy
	end
	return result
end

--- The form written to the settings file.
function M.serialize(settings)
	local result = {enabled = settings.enabled, targets = {}}
	for _, key in ipairs(SETTING_KEYS) do result[key] = settings[key] end
	for index, target in ipairs(settings.targets) do
		local copy = {}
		for _, key in ipairs(TARGET_KEYS) do copy[key] = target[key] end
		result.targets[index] = copy
	end
	return result
end

--- How many settings or targets differ; the on/off switch isn't a pending edit.
function M.change_count(saved, edited)
	local count = 0
	for _, key in ipairs(SETTING_KEYS) do
		if saved[key] ~= edited[key] then count = count + 1 end
	end
	local saved_by_id = {}
	for _, target in ipairs(saved.targets) do saved_by_id[target.id] = target end
	for _, target in ipairs(edited.targets) do
		local original = saved_by_id[target.id]
		saved_by_id[target.id] = nil
		local differs = not original
		for _, key in ipairs(TARGET_KEYS) do
			differs = differs or original[key] ~= target[key]
		end
		if differs then count = count + 1 end
	end
	for _ in pairs(saved_by_id) do count = count + 1 end
	return count
end

function M.replace_targets(settings, list)
	settings.targets = {}
	for _, target in ipairs(list) do add_target(settings, target) end
	targets.sort(settings.targets)
end

local function find_target(settings, id)
	for index, target in ipairs(settings.targets) do
		if target.id == id then return target, index end
	end
end

local function find_resolution(info, label)
	for _, resolution in ipairs(info.options.resolutions) do
		if resolution.label == label then return resolution end
	end
end

local function add_free_target(settings, info)
	local options = info.options
	for _, resolution in ipairs(options.resolutions) do
		for _, fps in ipairs(options.frame_rates) do
			if not has_limits(settings, resolution.width, resolution.height, fps) then
				add_target(settings, {
					max_width = resolution.width,
					max_height = resolution.height,
					max_fps = fps,
					method = info.choices[1] and info.choices[1].id or targets.NONE,
					multiplier = options.multipliers[1],
				})
				targets.sort(settings.targets)
				return nil
			end
		end
	end
	return ALL_USED
end

local function set_target_field(settings, target, field, value, info)
	local width, height, fps = target.max_width, target.max_height, target.max_fps
	if field == "resolution" then
		local resolution = find_resolution(info, value)
		width, height = resolution.width, resolution.height
	elseif field == "max_fps" then
		fps = value
	else
		target[field] = value
		return nil
	end
	if has_limits(settings, width, height, fps, target.id) then return DUPLICATE end
	target.max_width, target.max_height, target.max_fps = width, height, fps
	targets.sort(settings.targets)
	return nil
end

--- Apply one menu action; returns a message if it was refused.
function M.apply(settings, action, info)
	local operation = action[1]
	if operation == "set" then
		settings[action[2]] = action[3]
	elseif operation == "add_target" then
		return add_free_target(settings, info)
	elseif operation == "set_target" then
		local target = find_target(settings, action[2])
		if target then return set_target_field(settings, target, action[3], action[4], info) end
	elseif operation == "delete_target" then
		local _, index = find_target(settings, action[2])
		if index then table.remove(settings.targets, index) end
	end
	return nil
end

return M
