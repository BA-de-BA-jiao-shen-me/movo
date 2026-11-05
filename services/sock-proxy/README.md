# sock-proxy

唯一挂载 `/var/run/docker.sock` 的服务。sandbox-manager 通过 `MOVO_DOCKER_HOST=http://sock-proxy:2375` 访问 Docker。

- 路径/方法白名单：`app/rules.py`
- 容器创建请求体默认拒绝校验（Runtime 必须为 `MOVO_SANDBOX_RUNTIME`、禁止特权/CapAdd/Devices/Binds/宿主命名空间、挂载仅限沙箱卷且必须 Subpath、镜像前缀白名单、必须带 `movo.project` 标签）：`app/validate_create.py`
- 对已有容器/网络/exec 的操作先检查 movo 标签
- `MOVO_TENANT_MODE=multi` + `MOVO_SANDBOX_RUNTIME=runc` 直接拒绝启动

测试：`python -m pytest services/sock-proxy/tests`
