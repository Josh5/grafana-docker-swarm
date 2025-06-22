--[[
--File: apply-standard-record-formatting.lua
--Project: fluent-bit
--File Created: Tuesday, 29th October 2024 3:18:29 pm
--Author: Josh5 (jsunnex@gmail.com)
-------
--Last Modified: Monday, 23rd June 2025 3:20:42 am
--Modified By: Josh.5 (jsunnex@gmail.com)
--]]


local function to_unix_timestamp(ts)
    if type(ts) == "number" then
        -- Check for valid(ish) epoch timestamp. 32503680000 = Jan 1,3000
        if ts > 0 and ts < 32503680000 then
            -- Convert the number to a string to check for nanoseconds
            local ts_str = tostring(ts)
            if not ts_str:find("%.") then
                -- No decimal point, add .000000000 for nanoseconds
                ts_str = ts_str .. ".000000000"
            else
                -- Ensure the nanoseconds are padded to 9 digits
                local seconds, nanoseconds = ts_str:match("^(%d+)%.(%d+)$")
                nanoseconds = nanoseconds or ""
                nanoseconds = nanoseconds .. string.rep("0", 9 - #nanoseconds)
                ts_str = seconds .. "." .. nanoseconds
            end
            return tonumber(ts_str)
        end
        return ts
    elseif type(ts) == "string" then
        -- Try parsing ISO 8601 with optional fractional seconds
        local pattern = "(%d+)%-(%d+)%-(%d+)T(%d+):(%d+):(%d+%.?%d*)Z"
        local year, month, day, hour, min, sec = ts:match(pattern)
        if year then
            local sec_int, sec_frac = math.modf(tonumber(sec))
            local time_table = {
                year = tonumber(year),
                month = tonumber(month),
                day = tonumber(day),
                hour = tonumber(hour),
                min = tonumber(min),
                sec = sec_int
            }
            local utc = os.time(time_table)
            if utc then
                return tonumber(string.format("%d.%09d", utc, math.floor(sec_frac * 1e9)))
            end
        end
    end
    return nil
end

function standard_record_formatting(tag, timestamp, record)
    -- Create a new record
    local new_record = {}
    for key, value in pairs(record) do
        new_record[key] = value

        -- Convert any "source." keys to "source_". Graylog will do this already, but lets do it here so anything
        --  sent to another index not managed by Graylog will be uniform.
        if key:sub(1, 7) == "source." then
            local new_key = "source_" .. key:sub(8)
            new_record[new_key] = value
            new_record[key] = nil -- Remove the original "source." key
        end

        -- Check if message key is not lowercase
        if not new_record["message"] and key:lower() == "message" then
            new_record["message"] = value
            new_record[key] = nil -- Remove the original non-lowercase "message" record
        end
    end

    -- Check if "short_message" exists and is a non-empty string
    if new_record["short_message"] == "" then
        new_record["short_message"] = nil -- Remove if it's an empty string
    end

    -- Ensure "message" exists; if not, create one with "NO MESSAGE" or from "log"
    if not new_record["message"] or (type(new_record["message"]) ~= "string" or new_record["message"] == "") then
        if new_record["log"] and type(new_record["log"]) == "string" and new_record["log"] ~= "" then
            new_record["message"] = new_record["log"] -- Use log if it exists and is not empty
            new_record["log"] = nil -- Remove "log" after transferring its value
        else
            new_record["message"] = "NO MESSAGE" -- Default to "NO MESSAGE"
        end
    end

    -- Ensure "source" exists; if not, create one with "unknown" or from tag
    if not new_record["source"] or type(new_record["source"]) ~= "string" or new_record["source"] == "" then
        if tag and (type(tag) == "string" and tag ~= "") then
            new_record["source"] = tag -- Use tag if it exists and is not empty
        else
            new_record["source"] = "unknown" -- Default to "unknown"
        end
    end

    -- Normalize timestamp to nanosecond float precision
    new_record["timestamp"] = to_unix_timestamp(new_record["timestamp"]) or timestamp
    timestamp = new_record["timestamp"]

    -- Handle log level normalization and convert "level" to syslog standard int
    if new_record["level"] then
        local level_map = {
            [0] = "fatal",
            [1] = "alert",
            [2] = "critical",
            [3] = "error",
            [4] = "warn",
            [5] = "notice",
            [6] = "info",
            [7] = "debug",
            fatal = 0,
            emerg = 0,
            emergency = 0,
            alert = 1,
            crit = 2,
            critical = 2,
            err = 3,
            eror = 3,
            error = 3,
            warn = 4,
            warning = 4,
            notice = 5,
            informational = 6,
            information = 6,
            info = 6,
            dbug = 7,
            debug = 7,
            trace = 7
        }

        if type(tonumber(new_record["level"])) == "number" then
            -- Assign levelname based on the numeric level
            local numeric_level = tonumber(new_record["level"])
            new_record["levelname"] = level_map[numeric_level] or "unknown"
        else
            local level_string = tostring(new_record["level"]):gsub("^%s*(.-)%s*$", "%1") -- Trim whitespace
            -- Convert level string to lowercase for case-insensitive matching
            local lower_level_string = level_string:lower()

            -- Check if the lowercased level string exists in the map
            if level_map[lower_level_string] then
                new_record["level"] = level_map[lower_level_string] -- Set level to the corresponding integer
                new_record["levelname"] = lower_level_string -- Move the original level string to "levelname"
            else
                new_record["level"] = 6 -- Default to Info if level is unrecognized
                new_record["levelname"] = "info" -- Move original level string to "levelname"
            end
        end
    else
        new_record["level"] = 6 -- Default to Info if level is unrecognized
        new_record["levelname"] = "info" -- Move original level string to "levelname"
    end

    -- Ensure "service_name" exists. 
    -- A service name is something required by Open Telemetry semantic conventions. Grafana will automatically apply this as "fluent-bit" if it does not exist.
    if not new_record["service_name"] or
        (type(new_record["service_name"]) ~= "string" or new_record["service_name"] == "") then
        if new_record["source_service"] and
            (type(new_record["source_service"]) == "string" and new_record["source_service"] ~= "") then
            new_record["service_name"] = new_record["source_service"] -- Use "source_service" record if it exists and is not empty
        else
            new_record["service_name"] = new_record["source"] -- Default to "source" record
        end
    end

    -- Return the modified new_record
    return 1, timestamp, new_record
end
