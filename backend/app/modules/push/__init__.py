"""推送通道模块（W4）：租户事件推送（企业微信/钉钉/通用 webhook）。

- ``models``：push_channels 表（URL 视同密钥，Fernet 密文入库）；
- ``service``：CRUD + ``dispatch`` 事件发送（SSRF 双检、熔断、限频）；
- ``router``：/settings/push-channels CRUD（tenant_admin）。
"""
