-- Builds the MPV Enhanced menu as uosc menu data. Pure logic, no mpv calls.

local draft = require("draft")
local targets = require("targets")

local M = {}

local function method_title(info, id)
	if id == targets.NONE then return "None" end
	for _, choice in ipairs(info.choices) do
		if choice.id == id then return choice.title end
	end
	return id
end

local function fps_text(fps)
	return string.format("%g fps", fps)
end

local function multiplier_text(multiplier)
	return string.format("×%g", multiplier)
end

local function resolution_label(info, target)
	for _, resolution in ipairs(info.options.resolutions) do
		if resolution.width == target.max_width and resolution.height == target.max_height then
			return resolution.label
		end
	end
	return string.format("%gx%g", target.max_width, target.max_height)
end

--- For example "Up to 4K · 30 fps".
function M.target_label(info, target)
	return "Up to " .. resolution_label(info, target) .. " · " .. fps_text(target.max_fps)
end

--- For example "RTX Medium ×2", or just "None".
function M.result_text(info, method, multiplier)
	if method == targets.NONE then return "None" end
	return method_title(info, method) .. " " .. multiplier_text(multiplier)
end

local function note(title)
	return {title = title, selectable = false, muted = true, italic = true}
end

local function pick_list(values, current, title_of, action_of)
	local items = {}
	for index, value in ipairs(values) do
		items[index] = {
			title = title_of(value),
			value = action_of(value),
			active = value == current,
		}
	end
	return items
end

local function method_row(title, info, current, action_of)
	local values = {}
	for index, choice in ipairs(info.choices) do values[index] = choice.id end
	values[#values + 1] = targets.NONE
	return {
		title = title,
		hint = method_title(info, current),
		items = pick_list(values, current, function(id) return method_title(info, id) end, action_of),
	}
end

local function multiplier_row(title, info, method, current, action_of)
	local row = {
		title = title,
		hint = multiplier_text(current),
		items = pick_list(info.options.multipliers, current, multiplier_text, action_of),
	}
	if method == targets.NONE then
		row.selectable = false
		row.muted = true
	end
	return row
end

local function target_row(info, target, is_current)
	local id = target.id
	local function setter(field)
		return function(value) return {"set_target", id, field, value} end
	end
	local labels = {}
	for index, resolution in ipairs(info.options.resolutions) do labels[index] = resolution.label end
	return {
		id = id,
		title = (is_current and "● " or "") .. M.target_label(info, target),
		hint = M.result_text(info, target.method, target.multiplier),
		items = {
			{
				title = "Max resolution",
				hint = resolution_label(info, target),
				items = pick_list(
					labels,
					resolution_label(info, target),
					function(label) return label end,
					setter("resolution")
				),
			},
			{
				title = "Max frame rate",
				hint = fps_text(target.max_fps),
				items = pick_list(info.options.frame_rates, target.max_fps, fps_text, setter("max_fps")),
			},
			method_row("Method", info, target.method, setter("method")),
			multiplier_row("Multiplier", info, target.method, target.multiplier, setter("multiplier")),
			{title = "Delete target", value = {"delete_target", id}},
		},
	}
end

local function targets_row(info, edited, status)
	local items = {}
	for index, target in ipairs(edited.targets) do
		items[index] = target_row(info, target, status ~= nil and status.target_id == target.id)
	end
	if #items > 0 then items[#items].separator = true end
	items[#items + 1] = {title = "Add target", value = {"add_target"}}
	return {
		id = "targets",
		title = "Target overrides",
		hint = tostring(#edited.targets),
		items = items,
	}
end

local function cutoff_row(info, edited)
	local values = {}
	for index, value in ipairs(info.options.fps_cutoffs) do values[index] = value end
	values[#values + 1] = 0
	local function title(value) return value == 0 and "Off" or fps_text(value) end
	return {
		title = "FPS cutoff",
		hint = title(edited.fps_cutoff),
		items = pick_list(values, edited.fps_cutoff, title, function(value)
			return {"set", "fps_cutoff", value}
		end),
	}
end

function M.build(saved, edited, info, status)
	local menu = {
		type = "mpv-enhanced",
		title = "MPV Enhanced",
		callback = {"mpv_enhanced", "menu-event"},
		keep_open = true,
	}
	if #info.choices == 0 then
		menu.items = {note("No interpolation methods are installed. Run mpve profile to add one.")}
		return menu
	end

	local has_targets = #edited.targets > 0
	local default_method = method_row("Default method", info, edited.default_method, function(id)
		return {"set", "default_method", id}
	end)
	local default_multiplier = multiplier_row(
		"Default multiplier",
		info,
		edited.default_method,
		edited.default_multiplier,
		function(value) return {"set", "default_multiplier", value} end
	)
	if has_targets then
		default_method.muted = true
		default_multiplier.muted = true
	end

	local items = {
		{
			title = "Interpolation",
			hint = saved.enabled and "On" or "Off",
			value = {"toggle"},
			separator = true,
		},
		default_method,
		default_multiplier,
	}
	if has_targets then
		items[#items + 1] = note("Only used while there are no target overrides")
	end
	items[#items + 1] = cutoff_row(info, edited)
	items[#items + 1] = targets_row(info, edited, status)
	items[#items + 1] = {title = "Run benchmark", value = {"benchmark"}, separator = true}

	if status then
		items[#items + 1] = note(status.source)
		local using = note(status.using)
		using.separator = true
		items[#items + 1] = using
	end

	local count = draft.change_count(saved, edited)
	items[#items + 1] = {
		title = "Apply",
		hint = count == 1 and "1 change" or (count .. " changes"),
		value = {"apply"},
		selectable = count > 0,
		bold = count > 0,
	}
	items[#items + 1] = {title = "Discard changes", value = {"discard"}, selectable = count > 0}
	menu.items = items
	return menu
end

return M
