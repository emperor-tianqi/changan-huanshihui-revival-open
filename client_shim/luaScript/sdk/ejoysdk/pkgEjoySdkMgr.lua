-- Local-development SDK identity override for the unmodified 0.1.20.1 client.
--
-- Deployment target:
--   <persistentDataPath>/update/luaScript/sdk/ejoysdk/pkgEjoySdkMgr.lua
--
-- This replaces only the Lua-facing Ejoy manager. It deliberately does not
-- load ejoysdk_lua, contact a vendor, or dispatch SDK_LOGIN_RESULT. The
-- normal server-select panel will receive EJOY_SDK_LOAD_SERVER, download the
-- local server list, and keep its existing login button/c2s path.

doNameSpace("pkgEjoySdkMgr")

local LOCAL_INITED = 1
local LOCAL_LOGGED_OUT = 0
local LOCAL_LOGIN_OK = 1

local LOCAL_USER_ID = "local-dev-user"
local LOCAL_TOKEN = "local-dev-token"
local LOCAL_VERIFY_MD5 = "local-dev-proof"
local LOCAL_CREATOR = "local-dev"
local LOCAL_USERNAME = "local-dev"
local LOCAL_CHANNEL_ID = "local-dev-channel"
local LOCAL_SUBCHANNEL_ID = "0"

le_initedFlag = le_initedFlag or LOCAL_LOGGED_OUT
le_loginFlag = le_loginFlag or LOCAL_LOGGED_OUT
le_gameToken = le_gameToken or nil

function _setInitedFlag(flagValue)
    le_initedFlag = flagValue
end

function _setLoginFlag(flagValue)
    le_loginFlag = flagValue
end

function _setGameToken(value)
    le_gameToken = value
end

function getInitedFlag()
    return le_initedFlag
end

function getLoginFlag()
    return le_loginFlag
end

function getToken()
    return le_gameToken
end

function getEjoysdkUId()
    return LOCAL_USER_ID
end

function getAligamesChannelId()
    return LOCAL_CHANNEL_ID
end

function getAligamesSubChanneldId()
    return LOCAL_SUBCHANNEL_ID
end

function isCanShowUserCenter()
    return false
end

function isEjoyBBG()
    return false
end

-- Kept as a named no-op because the original manager exposes it and some
-- startup paths call it indirectly.
function requireEjoySdk()
    print("[LOCAL SDK BYPASS] vendor SDK load suppressed")
end

function tryToInitEjoySdk(onInitedCallback)
    print("[LOCAL SDK BYPASS] init local SDK identity")
    _setInitedFlag(LOCAL_INITED)
    pkgSDKDataMgr.setSdkInited(true)

    -- Preserve the original init -> SDK_INIT_SUCCEED -> login ordering, but
    -- leave the init caller before dispatching the event.
    pkgTimerMgr.once(1, function()
        pkgPlatformSDKMgr.sdkInitCallback()
    end)
end

local function seedLocalIdentity()
    pkgSDKDataMgr.setSDKUserName(LOCAL_USERNAME)
    pkgSDKDataMgr.setToken(LOCAL_TOKEN)
    pkgSDKDataMgr.setUserId(LOCAL_USER_ID)
    pkgSDKDataMgr.setCreator(LOCAL_CREATOR)
    pkgSDKDataMgr.setVerifyMd5(LOCAL_VERIFY_MD5)
    pkgSDKDataMgr.setSDKServerTime(os.time())
    _setGameToken(LOCAL_TOKEN)
    _setLoginFlag(LOCAL_LOGIN_OK)
end

function tryToLogin()
    print("[LOCAL SDK BYPASS] fixed local SDK login success")
    seedLocalIdentity()
    pkgPlatformSDKMgr.removeSDKLoginTips()

    -- Do not send SDK_LOGIN_RESULT here. The stock Ejoy handler reacts to a
    -- successful SDK_LOGIN_RESULT by calling onLoginHandler immediately; at
    -- that point the server list is not loaded yet and it re-enters loginSDK.
    -- This event is the stock Ejoy path that starts server-list loading.
    pkgTimerMgr.once(1, function()
        print("[LOCAL SDK BYPASS] dispatch EJOY_SDK_LOAD_SERVER")
        pkgEventMgr.dispatch(pkgEventType.EJOY_SDK_LOAD_SERVER)
    end)
end

function tryToLogOut()
    print("[LOCAL SDK BYPASS] logout")
    _setLoginFlag(LOCAL_LOGGED_OUT)
    _setGameToken(nil)
end

function tryToExit()
    print("[LOCAL SDK BYPASS] exit suppressed")
end

function tryToSetRoleData(strMethodName, tbSetRoleData)
    -- Role/payment SDK reporting is intentionally outside this milestone.
    print("[LOCAL SDK BYPASS] role callback suppressed", strMethodName)
end

function tryToPay(tbArgs)
    print("[LOCAL SDK BYPASS] payment suppressed")
    return false
end

function bindPushToken(accountid, playerid, serverid)
    return false
end

function getUtDid()
    return nil
end

function commitEvent(strEventName, tbEventParams)
    return false
end

function getJFUploadUrl()
    return nil
end

function showCustomerService()
    return false
end

function showUserCenter()
    return false
end

function clearUploadError()
end

function uploadError(logType, strError, strStackTrace, extra, strMd5)
    return false
end

function addUploadErrorHeader()
    return "local-dev"
end
