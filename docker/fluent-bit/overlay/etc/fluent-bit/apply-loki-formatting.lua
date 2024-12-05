--[[
--File: apply-loki-formatting.lua
--Project: fluent-bit
--File Created: Friday, 6th December 2024 7:35:45 am
--Author: Josh5 (jsunnex@gmail.com)
-------
--Last Modified: Friday, 6th December 2024 8:20:36 am
--Modified By: Josh5 (jsunnex@gmail.com)
--]]


function grafana_loki_formatting(tag, timestamp, record)
    -- Create a new record
    local new_record = {}
    for key, value in pairs(record) do
        new_record[key] = value

        -- Convert any "source." keys to "source_".
        if key:sub(1, 7) == "source." then
            local new_key = "source_" .. key:sub(8)
            new_record[new_key] = value
            new_record[key] = nil   -- Remove the original "source." key
        end
    end

    -- Ensure "source" exists; if not, create one with "unknown" or from tag
    if not new_record["source"] or (type(new_record["source"]) ~= "string" or new_record["source"] == "") then
        if tag and (type(tag) == "string" and tag ~= "") then
            new_record["source"] = tag          -- Use tag if it exists and is not empty
        else
            new_record["source"] = "unknown"    -- Default to "unknown"
        end
    end

    -- Extract and validate the "timestamp" field
    local record_timestamp = new_record["timestamp"]
    if type(record_timestamp) == "number" then
      -- Check for valid(ish) epoch timestamp. 32503680000 = Jan 1,3000
      if record_timestamp > 0 or record_timestamp < 32503680000 then
        -- Convert the number to a string to check for nanoseconds
        local record_timestamp_string = tostring(record_timestamp)
        if not record_timestamp_string:find("%.") then
            -- No decimal point, add .000000000 for nanoseconds
            record_timestamp_string = record_timestamp_string .. ".000000000"
        else
            -- Ensure the nanoseconds are padded to 9 digits
            local seconds, nanoseconds = record_timestamp_string:match("^(%d+)%.(%d+)$")
            nanoseconds = nanoseconds or ""
            nanoseconds = nanoseconds .. string.rep("0", 9 - #nanoseconds)
            record_timestamp_string = seconds .. "." .. nanoseconds
        end
    
        -- Convert back to number for returning as timestamp
        timestamp = tonumber(record_timestamp_string)
      end
    end

    -- Return the modified new_record
    return 1, timestamp, new_record
end
