-- The MPV Enhanced menu: connects mpv and uosc to the pure modules beside it.

local mp = require("mp")
local utils = require("mp.utils")
local draft = require("draft")
local menu = require("menu")
local targets = require("targets")

local LABEL = "@interpolation"
local SETTINGS_PATH = mp.command_native({"expand-path", "~~/script-opts/mpv_enhanced.json"})
local INFO_PATH = mp.command_native({"expand-path", "~~/script-opts/mpv_enhanced_install.json"})

local info, saved, edited
-- The user-data of the filter currently in the chain, if any.
local applied

local function read_file(path)
	local file = io.open(path, "rb")
	if not file then return nil end
	local text = file:read("*a")
	file:close()
	return text
end

--- A table, nil if the file is missing, or false if it can't be parsed.
local function read_json(path)
	-- An interrupted save leaves the complete new file at .tmp.
	local text = read_file(path) or read_file(path .. ".tmp")
	if text == nil then return nil end
	return utils.parse_json(text) or false
end

local function save()
	local temporary = SETTINGS_PATH .. ".tmp"
	local file = io.open(temporary, "wb")
	if not file then
		mp.osd_message("MPV Enhanced: could not save settings", 4)
		return
	end
	file:write(utils.format_json(draft.serialize(saved)))
	file:close()
	-- Windows can't rename over an existing file.
	if not os.rename(temporary, SETTINGS_PATH) then
		os.remove(SETTINGS_PATH)
		os.rename(temporary, SETTINGS_PATH)
	end
end

local function source_params()
	local width = mp.get_property_number("width")
	local height = mp.get_property_number("height")
	local fps = mp.get_property_number("container-fps")
	return width, height, fps
end

local function is_moving_video()
	local track = mp.get_property_native("current-tracks/video")
	return track and not track.image and not track.albumart
end

local function choice_uses_tensorrt(id)
	for _, choice in ipairs(info.choices) do
		if choice.id == id then return choice.settings.tensorrt == true end
	end
	return false
end

--- Always asynchronous: a synchronous command runs on this script's own thread,
--- which makes VapourSynth start Python there and crashes mpv when it exits.
local function filter_command(action, argument, on_done)
	mp.command_native_async({"vf", action, argument}, on_done or function() end)
end

local function remove_filter()
	if applied then
		filter_command("remove", LABEL)
		applied = nil
	end
end

--- Make the filter chain match the settings for the current file.
local function apply_filter()
	if not mp.get_property("path") then return end
	local width, height, fps = source_params()
	local resolved = saved.enabled and is_moving_video()
		and targets.resolve(saved, width, height, fps) or nil
	local wanted = resolved
		and string.format("%s/%g", resolved.method, resolved.multiplier) or nil
	if wanted == applied then return end

	remove_filter()
	if not resolved then
		if saved.enabled then mp.osd_message("Interpolation: None", 2) end
		return
	end
	if choice_uses_tensorrt(resolved.method) then
		mp.osd_message("Building TensorRT engine, this can take minutes", 5)
	end
	local filter = LABEL .. ":vapoursynth=file=~~/interpolation.vpy:user-data=" .. wanted
	applied = wanted
	filter_command("add", filter, function(succeeded)
		if succeeded then
			mp.osd_message("Interpolation: " .. menu.result_text(info, resolved.method, resolved.multiplier), 2)
			return
		end
		if applied == wanted then applied = nil end
		mp.osd_message("Interpolation failed", 4)
	end)
end

--- What the menu's status rows say about the current file.
local function current_status()
	local width, height, fps = source_params()
	if not mp.get_property("path") or not width or not height then return nil end
	local source = fps and string.format("Playing: %d×%d · %.4g fps", width, height, fps)
		or string.format("Playing: %d×%d", width, height)
	local resolved = fps and is_moving_video() and targets.resolve(saved, width, height, fps) or nil
	local target = fps and #saved.targets > 0 and targets.match(saved.targets, width, height, fps) or nil
	local using
	if resolved and target then
		using = "Using: " .. menu.target_label(info, target)
			.. " → " .. menu.result_text(info, resolved.method, resolved.multiplier)
	elseif resolved then
		using = "Using: " .. menu.result_text(info, resolved.method, resolved.multiplier)
	else
		using = "Using: None"
	end
	return {source = source, using = using, target_id = target and target.id or nil}
end

local function menu_json()
	return utils.format_json(menu.build(saved, edited, info, current_status()))
end

local function refresh_menu()
	mp.commandv("script-message-to", "uosc", "update-menu", menu_json())
end

local function toggle()
	saved.enabled = not saved.enabled
	edited.enabled = saved.enabled
	save()
	if saved.enabled then
		apply_filter()
	else
		remove_filter()
		mp.osd_message("Interpolation: Off", 2)
	end
	refresh_menu()
end

local function apply_edits()
	edited.enabled = saved.enabled
	saved = draft.copy(edited)
	save()
	apply_filter()
	refresh_menu()
end

local actions = {
	toggle = toggle,
	apply = apply_edits,
	discard = function()
		edited = draft.copy(saved)
		refresh_menu()
	end,
}

local function on_menu_event(json)
	local event = utils.parse_json(json)
	if not event or event.type ~= "activate" or type(event.value) ~= "table" then return end
	local action = actions[event.value[1]]
	if action then return action(event.value) end
	local refused = draft.apply(edited, event.value, info)
	if refused then mp.osd_message(refused, 3) end
	refresh_menu()
end

info = read_json(INFO_PATH) or {choices = {}}
do
	local warning
	saved, warning = draft.normalize(read_json(SETTINGS_PATH), info)
	edited = draft.copy(saved)
	if warning then mp.osd_message(warning, 5) end
end

mp.register_event("file-loaded", apply_filter)
mp.register_script_message("toggle", toggle)
mp.register_script_message("menu-event", on_menu_event)
mp.register_script_message("open-menu", function()
	mp.commandv("script-message-to", "uosc", "open-menu", menu_json())
end)
