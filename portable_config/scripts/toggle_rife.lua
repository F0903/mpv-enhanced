local mp = require "mp"

local rife_enabled = false

function toggle_rife()
    if rife_enabled then
        -- mpv cannot un-apply a profile, so remove the filter the profile added.
        mp.command("vf remove @rife")

        mp.osd_message("RIFE Interpolation: OFF", 2)
    else
        mp.command("apply-profile rife")

        mp.osd_message("RIFE Interpolation: ON", 2)
    end

    rife_enabled = not rife_enabled
end

mp.register_script_message("toggle_rife", toggle_rife)
