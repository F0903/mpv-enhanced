local Element = require('elements/Element')
local dots = {'.', '..', '...'}

local function cleanup_output(output)
	return tostring(output):gsub('%c*\n%c*', '\n'):match('^[%s%c]*(.-)[%s%c]*$')
end

---@class Updater : Element
local Updater = class(Element)

function Updater:new() return Class.new(self) --[[@as Updater]] end
function Updater:init()
	Element.init(self, 'updater', {render_order = 1000})
	self.output = nil
	self.title = ''
	self.state = 'circle' -- Also used as an icon name. 'pending' maps to 'spinner'.
	self.update_available = false

	-- Buttons
	self.check_button = {method = 'check', title = t('Check for updates')}
	self.update_button = {method = 'update', title = t('Update uosc'), color = config.color.success}
	self.changelog_button = {method = 'open_changelog', title = t('Open changelog')}
	self.close_button = {method = 'destroy', title = t('Close') .. ' (Esc)', color = config.color.error}
	self.quit_button = {method = 'quit', title = t('Quit')}
	self.buttons = {self.check_button, self.close_button}
	self.selected_button_index = 1

	-- Key bindings
	self:add_key_binding('right', 'select_next_button')
	self:add_key_binding('tab', 'select_next_button')
	self:add_key_binding('left', 'select_prev_button')
	self:add_key_binding('shift+tab', 'select_prev_button')
	self:add_key_binding('enter', 'activate_selected_button')
	self:add_key_binding('kp_enter', 'activate_selected_button')
	self:add_key_binding('esc', 'destroy')

	Elements:maybe('curtain', 'register', self.id)
	self:check()
end

function Updater:destroy()
	Elements:maybe('curtain', 'unregister', self.id)
	Element.destroy(self)
end

function Updater:quit()
	mp.command('quit')
end

function Updater:select_prev_button()
	self.selected_button_index = self.selected_button_index - 1
	if self.selected_button_index < 1 then self.selected_button_index = #self.buttons end
	request_render()
end

function Updater:select_next_button()
	self.selected_button_index = self.selected_button_index + 1
	if self.selected_button_index > #self.buttons then self.selected_button_index = 1 end
	request_render()
end

function Updater:activate_selected_button()
	local button = self.buttons[self.selected_button_index]
	if button then self[button.method](self) end
end

---@param msg string
function Updater:append_output(msg)
	self.output = (self.output or '') .. ass_escape('\n' .. cleanup_output(msg))
	request_render()
end

---@param msg string
function Updater:display_error(msg)
	self.state = 'error'
	self.title = t('An error has occurred.') .. ' ' .. t('See console for details.')
	self:append_output(msg)
	print(msg)
end

function Updater:open_changelog()
	if self.state == 'pending' then return end

	local url = 'https://github.com/tomasklaen/uosc/releases'

	self:append_output('Opening URL: ' .. url)

	call_ziggy_async({'open', url}, function(error)
		if error then
			self:display_error(error)
			return
		end
	end)
end

function Updater:check()
	self:update()
end

function Updater:update()
	self.state = 'done'
	self.title = 'uosc updates are managed by mpv-enhanced releases'
	self.output = nil
	self.buttons = {self.quit_button, self.close_button}
	self.selected_button_index = 1
	request_render()
end

function Updater:render()
	local ass = assdraw.ass_new()

	local text_size = math.min(20 * state.scale, display.height / 20)
	local icon_size = text_size * 2
	local center_x = round(display.width / 2)

	local color = fg
	if self.state == 'done' or self.update_available then
		color = config.color.success
	elseif self.state == 'error' then
		color = config.color.error
	end

	-- Divider
	local divider_width = round(math.min(500 * state.scale, display.width * 0.8))
	local divider_half, divider_border_half, divider_y = divider_width / 2, round(1 * state.scale), display.height * 0.65
	local divider_ay, divider_by = round(divider_y - divider_border_half), round(divider_y + divider_border_half)
	ass:rect(center_x - divider_half, divider_ay, center_x - icon_size, divider_by, {
		color = color, border = options.text_border * state.scale, border_color = bg, opacity = 0.5,
	})
	ass:rect(center_x + icon_size, divider_ay, center_x + divider_half, divider_by, {
		color = color, border = options.text_border * state.scale, border_color = bg, opacity = 0.5,
	})
	if self.state == 'pending' then
		ass:spinner(center_x, divider_y, icon_size, {
			color = fg, border = options.text_border * state.scale, border_color = bg,
		})
	else
		ass:icon(center_x, divider_y, icon_size * 0.8, self.state, {
			color = color, border = options.text_border * state.scale, border_color = bg,
		})
	end

	-- Output
	local output = self.output or dots[math.ceil((mp.get_time() % 1) * #dots)]
	ass:txt(center_x, divider_y - icon_size, 2, output, {
		size = text_size, color = fg, border = options.text_border * state.scale, border_color = bg,
	})

	-- Title
	ass:txt(center_x, divider_y + icon_size, 5, self.title, {
		size = text_size, bold = true, color = color, border = options.text_border * state.scale, border_color = bg,
	})

	-- Buttons
	local outline = round(1 * state.scale)
	local spacing = outline * 9
	local padding = round(text_size * 0.5)

	local text_opts = {size = text_size, bold = true}

	-- Calculate button text widths
	local total_width = (#self.buttons - 1) * spacing
	for _, button in ipairs(self.buttons) do
		button.width = text_width(button.title, text_opts) + padding * 2
		total_width = total_width + button.width
	end

	-- Render buttons
	local ay = round(divider_y + icon_size * 1.8)
	local ax = round(display.width / 2 - total_width / 2)
	local height = text_size + padding * 2
	for index, button in ipairs(self.buttons) do
		local rect = {
			ax = ax,
			ay = ay,
			bx = ax + button.width,
			by = ay + height,
		}
		ax = rect.bx + spacing
		local is_hovered = get_point_to_rectangle_proximity(cursor, rect) <= 0

		-- Background
		ass:rect(rect.ax, rect.ay, rect.bx, rect.by, {
			color = button.color or fg,
			radius = state.radius,
			opacity = is_hovered and 1 or 0.8,
		})
		-- Selected outline
		if index == self.selected_button_index then
			ass:rect(rect.ax - outline * 4, rect.ay - outline * 4, rect.bx + outline * 4, rect.by + outline * 4, {
				border = outline,
				border_color = button.color or fg,
				radius = state.radius + outline * 4,
				opacity = {primary = 0, border = 0.5},
			})
		end
		-- Text
		local x, y = rect.ax + (rect.bx - rect.ax) / 2, rect.ay + (rect.by - rect.ay) / 2
		ass:txt(x, y, 5, button.title, {size = text_size, bold = true, color = fgt})

		cursor:zone('primary_down', rect, self:create_action(button.method))

		-- Select hovered button
		if is_hovered then self.selected_button_index = index end
	end

	return ass
end

return Updater
