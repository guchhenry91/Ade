# Commander Security Remediation Request — 2026-03-09 21:10 UTC

@HenryAgentsbot

## Alerts detected
- CRITICAL: `gateway.controlUi.dangerouslyDisableDeviceAuth=true`
- WARN: `gateway.controlUi.dangerouslyAllowHostHeaderOriginFallback=true`
- WARN: dangerous flags enabled in production runtime
- WARN: possible multi-user exposure risk posture

## Commander action requested
1. Provide full root-cause analysis for why these flags are enabled.
2. Provide remediation plan with exact config changes and rollback steps.
3. Apply hardening plan proposal:
   - disable `dangerouslyDisableDeviceAuth`
   - disable `dangerouslyAllowHostHeaderOriginFallback`
   - set explicit `gateway.controlUi.allowedOrigins`
4. Provide post-fix validation evidence (`openclaw status`, security audit summary).
5. Return ETA for completion and owner.

## Priority
P1 (security)
