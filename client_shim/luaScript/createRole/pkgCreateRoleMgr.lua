-- Narrow create-role submit diagnostic for the unmodified 0.1.20.1 client.
--
-- This preserves the recovered stock control flow.  It only emits markers;
-- it does not bypass validation, change the role name/career, or send any
-- packet that the stock client would not send.

doNameSpace("pkgCreateRoleMgr")

local function traceValue(value)
    if value == nil then
        return "<nil>"
    end
    return tostring(value)
end

print("[CREATE ROLE TRACE] pkgCreateRoleMgr override loaded")

local function installNativeSendTrace()
    if le_create_role_native_send_trace_installed then
        return
    end
    le_create_role_native_send_trace_installed = true

    local previousSendCallHook = native.sendCallHook
    native.sendCallHook = function(socket, protocolId, ...)
        local protocolInfo = nil
        if c2s and c2s.ptrls then
            protocolInfo = c2s.ptrls[protocolId]
        end
        local protocolName = protocolInfo and protocolInfo.funcString or "<unknown>"
        if protocolName == "createRole" or protocolName == "onLine"
            or protocolName == "onLine_forNetState" then
            print("[CREATE ROLE SEND TRACE] native writer enter protocol="
                .. traceValue(protocolName) .. " id=" .. traceValue(protocolId))
        end

        if previousSendCallHook then
            local result = { previousSendCallHook(socket, protocolId, ...) }
            if protocolName == "createRole" then
                print("[CREATE ROLE SEND TRACE] native writer returned")
            end
            return unpack(result)
        end

        local result = { native.netCallWrite(socket._netStream, protocolId, ...) }
        if protocolName == "createRole" then
            print("[CREATE ROLE SEND TRACE] native writer returned")
        end
        return unpack(result)
    end
    print("[CREATE ROLE SEND TRACE] native send hook installed")
end

function checkRoleName(strRoleName)
    print("[CREATE ROLE TRACE] checkRoleName.begin name=" .. traceValue(strRoleName))

    local dResult = pkgRoleConstMgr.checkRoleName(strRoleName)
    print("[CREATE ROLE TRACE] roleName.const_result=" .. traceValue(dResult))

    if dResult == pkgRoleConstMgr.ROLE_NAME_VALID.EMPTY then
        print("[CREATE ROLE TRACE] guard=empty result=reject")
        pkgUIAlertMgr.alert2(_T("create_role_name_empty"))
        return false
    end
    if dResult == pkgRoleConstMgr.ROLE_NAME_VALID.WRONG_TYPE then
        print("[CREATE ROLE TRACE] guard=wrong_type result=reject")
        pkgUIAlertMgr.alert2(_T("create_role_name_contain_error"))
        return false
    end
    if dResult == pkgRoleConstMgr.ROLE_NAME_VALID.LENGTH_LIMIT then
        print("[CREATE ROLE TRACE] guard=length_limit result=reject")
        pkgUIAlertMgr.alert2(_T("create_role_name_long"))
        return false
    end

    local bBlackName = pkgBlackWordMgr.isBlackName(strRoleName)
    print("[CREATE ROLE TRACE] guard=black_name value=" .. traceValue(bBlackName))
    local bSensitive = false
    -- Preserve the stock short-circuit: the sensitive-word lookup is not
    -- evaluated when the black-name guard has already rejected the name.
    if not bBlackName then
        bSensitive = pkgStringMgr.existSensitiveWord(
            strRoleName,
            pkgCfgFilterMgr.getRoleNameFilterCfg()
        )
    end
    print("[CREATE ROLE TRACE] guard=sensitive_word value=" .. traceValue(bSensitive))
    if not bBlackName and not bSensitive then
        print("[CREATE ROLE TRACE] roleName.validation=pass")
        return true
    end

    print("[CREATE ROLE TRACE] guard=harmonization result=reject")
    pkgUIAlertMgr.alert2(_T("create_role_name_hexie"))
    return false
end

function createRole()
    print("[CREATE ROLE TRACE] submit callback entered")
    installNativeSendTrace()

    local strRoleName = pkgCreateRoleDataMgr.getCreateRoleName()
    print("[CREATE ROLE TRACE] current roleName=" .. traceValue(strRoleName))
    if not checkRoleName(strRoleName) then
        print("[CREATE ROLE TRACE] final=blocked_by_role_name")
        return
    end

    local dCareer = pkgCreateRoleDataMgr.getCreateRoleCareer()
    print("[CREATE ROLE TRACE] current careerId=" .. traceValue(dCareer))
    if dCareer then
        local bConnected = false
        if e_tbSocket and e_tbSocket.isConnected then
            bConnected = e_tbSocket:isConnected()
        end
        print("[CREATE ROLE SEND TRACE] protocol resolved type="
            .. type(c2s.createRole))
        print("[CREATE ROLE SEND TRACE] connection=" .. traceValue(e_tbSocket)
            .. " connected=" .. traceValue(bConnected))
        print("[CREATE ROLE SEND TRACE] writer begin")
        print("[CREATE ROLE TRACE] final=call c2s.createRole name="
            .. traceValue(strRoleName) .. " career=" .. traceValue(dCareer))
        c2s.createRole(e_tbSocket, strRoleName, dCareer)
        print("[CREATE ROLE SEND TRACE] writer returned")
        return
    end

    print("[CREATE ROLE TRACE] guard=career_missing result=reject")
    pkgUIAlertMgr.alert2(_T("create_role_career_error"))
    print("[CREATE ROLE TRACE] final=blocked_by_career")
end

function s2c.returnCreateRoleResult(tbSocket, bCreateRole)
    print("[POST CREATE TRACE] returnCreateRoleResult entered value="
        .. traceValue(bCreateRole))

    local tbPlayerInfo = nil
    if pkgPlayerInfoMgr and pkgPlayerInfoMgr.getPlayerInfo then
        tbPlayerInfo = pkgPlayerInfoMgr.getPlayerInfo()
    end
    local dRoleID = nil
    if tbPlayerInfo then
        dRoleID = tbPlayerInfo.dRoleID
    end
    print("[POST CREATE TRACE] playerInfo.dRoleID=" .. traceValue(dRoleID))

    if bCreateRole then
        print("[POST CREATE TRACE] sdk createRole callback begin")
        pkgSDKPlayerInfoMgr.createRole()
        print("[POST CREATE TRACE] sdk createRole callback returned")
    end

    -- The recovered stock handler has no game-state transition here.  Keep
    -- this marker explicit so a device run can distinguish the SDK callback
    -- from a later server-driven login/snapshot transition.
    print("[POST CREATE TRACE] stock handler transition=none")
end

local function init()
end
