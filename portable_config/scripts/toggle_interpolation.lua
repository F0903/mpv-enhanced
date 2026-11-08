local mp = require "mp"

local enabled = false

function toggle_interpolation()
    if enabled then
        -- mpv cannot un-apply a profile, so remove the filter the profile added.
        mp.command("vf remove @interpolation")

        mp.osd_message("Frame Interpolation: OFF", 2)
    else
        mp.command("apply-profile interpolation")

        mp.osd_message("Frame Interpolation: ON", 2)
    end

    enabled = not enabled
end

mp.register_script_message("toggle_interpolation", toggle_interpolation)
