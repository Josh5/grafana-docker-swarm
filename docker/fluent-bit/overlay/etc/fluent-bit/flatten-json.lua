--[[
--File: flatten-json.lua
--Project: fluent-bit
--File Created: Tuesday, 29th October 2024 2:29:53 pm
--Author: Josh5 (jsunnex@gmail.com)
-------
--Last Modified: Wednesday, 30th October 2024 3:26:41 am
--Modified By: Josh5 (jsunnex@gmail.com)
--]]

local cjson = require "cjson"

-- Function to check if a string is valid JSON and decode it
local function is_json(str)
    local success, result = pcall(cjson.decode, str)
    return success, result  -- Return both success status and decoded result
end

function flatten_json(tag, timestamp, record)
    -- Create a new record to hold the flattened data
    local new_record = {}

    -- Iterate through each key in the original record
    for key, value in pairs(record) do
        -- Only attempt to decode if the value is a string
        if type(value) == "string" then
            local success, decoded_value = is_json(value)
            if success and type(decoded_value) == "table" then
                -- If the value is valid JSON, merge it into new_record
                for k, v in pairs(decoded_value) do
                    new_record[k] = v
                end
            else
                -- If it's not valid JSON, keep the original value
                new_record[key] = value
            end
        else
            -- If the value is not a string, keep the original value
            new_record[key] = value
        end
    end

    -- Create a new flat record to hold the final flattened data
    local flat_record = {}

    -- Function to recursively flatten the JSON
    local function flatten(parent, record, parent_key)
        for key, value in pairs(record) do
            local new_key = parent_key and (parent_key .. "." .. key) or key  -- Construct the new key

            if type(value) == "table" then
                if #value > 0 then  -- Check if it's a list
                    for index, item in ipairs(value) do
                        if type(item) == "table" then
                            flatten(parent, item, new_key .. "." .. index)  -- Flatten each item in the list
                        else
                            parent[new_key .. "." .. index] = item          -- Directly assign non-table items
                        end
                    end
                else
                    flatten(parent, value, new_key)     -- Flatten the object
                end
            else
                parent[new_key] = value                 -- Add to the flat record
            end
        end
    end

    -- Special handling for flattening any tables in the "message" key.
    -- These keys are applied directly to the root without being prefixed.
    if new_record["message"] then
        local message_value = new_record["message"]
        if type(message_value) == "table" then
            for k, v in pairs(message_value) do
                if type(v) ~= "table" then
                    flat_record[k] = v  -- Directly add non-table values
                else
                    -- Handle cases where it's a table (nested structure)
                    flatten(flat_record, v, "message")  -- Flatten any nested structure
                end
            end
        else
            flat_record["message"] = message_value  -- Assign the string value directly
        end
        new_record["message"] = nil  -- Remove original message after processing
    end

    -- Flatten the rest of the new_record.
    -- These keys will have their parents prepended. Eg {"httpRequest": {"country": "NZ"}} becomes {"httpRequest.country": "NZ"}.
    flatten(flat_record, new_record, nil)

    -- Return the flattened record
    return 1, timestamp, flat_record
end
