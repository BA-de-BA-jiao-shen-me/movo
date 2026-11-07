#!/usr/bin/env bash

movo_detect_locale() {
  local requested="${MOVO_LANG:-en}"
  requested="$(printf '%s' "${requested}" | tr '[:upper:]' '[:lower:]')"
  case "${requested}" in
    zh|zh-*|zh_*|cn|chinese)
      MOVO_LOCALE=zh
      ;;
    *)
      MOVO_LOCALE=en
      ;;
  esac
}

movo_usage() {
  if [[ "${MOVO_LOCALE}" == "zh" ]]; then
    printf '用法：\n'
    printf '  ./movo [--lang zh-CN|en] [--mirror cn|global] up  启动 MOVO 并输出初始化地址\n'
    printf '  ./movo up --build                  从源码构建并启动（不与 --mirror cn 合用）\n'
    printf '  ./movo build                          从源码构建 MOVO 镜像\n'
    printf '  ./movo update                         拉取当前版本镜像并更新服务\n'
    printf '  ./movo fix [--yes]                    备份并修复冲突的会话消息序号\n'
    printf '  ./movo backup [目录]                  停机一致性备份全部 MOVO 数据卷\n'
    printf '  ./movo restore <目录> --yes           校验并恢复 MOVO 数据卷\n'
    printf '  ./movo status                         查看服务状态\n'
    printf '  ./movo logs [服务名]                  查看日志\n'
    printf '  ./movo restart                        重启服务\n'
    printf '  ./movo down                           停止服务（保留数据卷）\n'
    printf '  ./movo down -v                        停止服务并删除全部 MOVO 数据\n'
  else
    printf 'Usage:\n'
    printf '  ./movo [--lang zh-CN|en] [--mirror cn|global] up  Start MOVO and print the setup URL\n'
    printf '  ./movo up --build                       Build and start from source (without --mirror cn)\n'
    printf '  ./movo build                           Build MOVO images from source\n'
    printf '  ./movo update                          Pull current images and update services\n'
    printf '  ./movo fix [--yes]                     Back up and repair conflicting chat sequences\n'
    printf '  ./movo backup [directory]              Back up all MOVO volumes while stopped\n'
    printf '  ./movo restore <directory> --yes       Verify and restore MOVO volumes\n'
    printf '  ./movo status                          Show service status\n'
    printf '  ./movo logs [service]                  Show logs\n'
    printf '  ./movo restart                         Restart services\n'
    printf '  ./movo down                            Stop services and preserve volumes\n'
    printf '  ./movo down -v                         Stop services and delete all MOVO data\n'
  fi
}

movo_msg() {
  local key="$1"
  shift
  case "${MOVO_LOCALE}:${key}" in
    zh:docker_missing) printf '错误：未找到 Docker，请先安装 Docker Engine 或 Docker Desktop。\n' ;;
    en:docker_missing) printf 'Error: Docker was not found. Install Docker Engine or Docker Desktop first.\n' ;;
    zh:compose_missing) printf '错误：未找到 Docker Compose v2。\n' ;;
    en:compose_missing) printf 'Error: Docker Compose v2 was not found.\n' ;;
    zh:docker_stopped) printf '错误：Docker 尚未运行，请先启动 Docker。\n' ;;
    en:docker_stopped) printf 'Error: Docker is not running. Start Docker first.\n' ;;
    zh:sandbox_config_mismatch) printf '错误：沙箱模式需要同时设置 COMPOSE_PROFILES=sandbox 和 MOVO_SANDBOX_MANAGER_URL=http://sandbox-manager:8300。\n' ;;
    en:sandbox_config_mismatch) printf 'Error: sandbox mode requires COMPOSE_PROFILES=sandbox and MOVO_SANDBOX_MANAGER_URL=http://sandbox-manager:8300 together.\n' ;;
    zh:sandbox_engine_old) printf '错误：沙箱工作区需要 Docker Engine 26 或更新版本，当前版本：%s。\n' "$1" ;;
    en:sandbox_engine_old) printf 'Error: sandbox workspaces require Docker Engine 26 or newer. Current version: %s.\n' "$1" ;;
    zh:sandbox_engine_range) printf '错误：Kata 沙箱只支持 Docker Engine 26.x/27.x（28+ 存在 Kata 容器无网卡问题 moby#52017），当前版本：%s。如确需尝试，设置 MOVO_DOCKER_ALLOW_UNTESTED=1（探针仍须通过）。\n' "$1" ;;
    en:sandbox_engine_range) printf 'Error: the Kata sandbox supports Docker Engine 26.x/27.x only (28+ leaves Kata containers without a NIC, moby#52017). Current: %s. Set MOVO_DOCKER_ALLOW_UNTESTED=1 to try anyway (the probe must still pass).\n' "$1" ;;
    zh:sandbox_engine_untested) printf '警告：Docker Engine %s 未经 Kata 验证，已按 MOVO_DOCKER_ALLOW_UNTESTED=1 继续，探针失败将拒绝执行。\n' "$1" ;;
    en:sandbox_engine_untested) printf 'Warning: Docker Engine %s is untested with Kata; continuing because MOVO_DOCKER_ALLOW_UNTESTED=1. Execution is refused if the probe fails.\n' "$1" ;;
    zh:sandbox_no_kvm) printf '错误：未检测到可用的 /dev/kvm，Kata 沙箱无法运行。请使用裸金属或开启嵌套虚拟化的主机；单租户可显式设置 MOVO_SANDBOX_RUNTIME=runc 与 MOVO_SANDBOX_ALLOW_RUNC=i-understand 降级。\n' ;;
    en:sandbox_no_kvm) printf 'Error: /dev/kvm is not available, so the Kata sandbox cannot run. Use bare metal or nested virtualisation; single-tenant installs may explicitly set MOVO_SANDBOX_RUNTIME=runc and MOVO_SANDBOX_ALLOW_RUNC=i-understand.\n' ;;
    zh:sandbox_no_kata) printf '错误：Docker 未注册 kata 运行时。运行 ./movo doctor 查看 daemon.json 配置示例。\n' ;;
    en:sandbox_no_kata) printf 'Error: Docker has no "kata" runtime registered. Run ./movo doctor for a daemon.json example.\n' ;;
    zh:sandbox_runtime_invalid) printf '错误：MOVO_SANDBOX_RUNTIME 只能是 kata 或 runc，当前：%s。\n' "$1" ;;
    en:sandbox_runtime_invalid) printf 'Error: MOVO_SANDBOX_RUNTIME must be kata or runc, got: %s.\n' "$1" ;;
    zh:sandbox_tenant_invalid) printf '错误：MOVO_TENANT_MODE 只能是 single 或 multi，当前：%s。\n' "$1" ;;
    en:sandbox_tenant_invalid) printf 'Error: MOVO_TENANT_MODE must be single or multi, got: %s.\n' "$1" ;;
    zh:sandbox_multi_runc) printf '错误：多租户模式禁止使用 runc 运行沙箱，无覆盖开关。请在有 KVM 的主机上使用 Kata。\n' ;;
    en:sandbox_multi_runc) printf 'Error: runc is forbidden in multi-tenant mode and cannot be overridden. Use Kata on a KVM host.\n' ;;
    zh:sandbox_runc_ack) printf '错误：降级到 runc 需要同时设置 MOVO_SANDBOX_ALLOW_RUNC=i-understand（仅单租户，会写审计并常驻告警）。\n' ;;
    en:sandbox_runc_ack) printf 'Error: falling back to runc requires MOVO_SANDBOX_ALLOW_RUNC=i-understand (single-tenant only; audited and permanently flagged).\n' ;;
    zh:sandbox_unavailable) printf '错误：沙箱管理服务未就绪。请执行 ./movo logs sandbox-manager 查看原因。\n' ;;
    en:sandbox_unavailable) printf 'Error: sandbox manager is not ready. Run ./movo logs sandbox-manager for details.\n' ;;
    zh:image_source_missing) printf '错误：无法确定公开镜像地址。请从 GitHub 克隆仓库、在 .env 设置 MOVO_IMAGE_PREFIX，或执行 ./movo up --build。\n' ;;
    en:image_source_missing) printf 'Error: public image location is unknown. Clone from GitHub, set MOVO_IMAGE_PREFIX in .env, or run ./movo up --build.\n' ;;
    zh:using_local_images) printf '未配置公开镜像地址，将使用现有的本地 MOVO 镜像。\n' ;;
    en:using_local_images) printf 'No public image location is configured; using existing local MOVO images.\n' ;;
    zh:update_local_only) printf '错误：当前仅配置了本地镜像。请先设置 MOVO_IMAGE_PREFIX，或使用 ./movo up --build 重新构建。\n' ;;
    en:update_local_only) printf 'Error: only local images are configured. Set MOVO_IMAGE_PREFIX first, or rebuild with ./movo up --build.\n' ;;
    zh:waiting) printf '\n正在等待服务健康检查' ;;
    en:waiting) printf '\nWaiting for deployment health checks' ;;
    zh:done) printf ' 完成\n' ;;
    en:done) printf ' done\n' ;;
    zh:timeout) printf '\n服务未在预期时间内全部就绪。请执行 ./movo status 和 ./movo logs 查看原因。\n' ;;
    en:timeout) printf '\nServices did not become ready in time. Run ./movo status and ./movo logs for details.\n' ;;
    zh:ready_title) printf '\nMOVO 已启动。请在浏览器中完成首次初始化：\n\n' ;;
    en:ready_title) printf '\nMOVO is running. Complete the initial setup in your browser:\n\n' ;;
    zh:starting) printf '正在启动 MOVO 服务...\n' ;;
    en:starting) printf 'Starting MOVO services...\n' ;;
    zh:building) printf '正在从源码构建 MOVO 镜像...\n' ;;
    en:building) printf 'Building MOVO images from source...\n' ;;
    zh:updating) printf '正在拉取 MOVO 镜像并更新服务...\n' ;;
    en:updating) printf 'Pulling MOVO images and updating services...\n' ;;
    zh:upgrading_database) printf '正在自动检查并升级会话分享数据库索引...\n' ;;
    en:upgrading_database) printf 'Checking and upgrading session-sharing database indexes...\n' ;;
    zh:upgrade_database_failed) printf '数据库升级未通过；旧服务保持运行，数据未被迁移脚本改写。请检查上方错误。\n' ;;
    en:upgrade_database_failed) printf 'Database upgrade did not pass; old services remain running and the migration did not rewrite data. Check the error above.\n' ;;
    zh:fix_confirm) printf '即将停止聊天服务并修改冲突会话的序号。已确认维护窗口后输入 FIX（自动化可用 --yes）：' ;;
    en:fix_confirm) printf 'This stops chat and changes conflicting sequence metadata. Type FIX to continue (or use --yes): ' ;;
    zh:fix_tools_missing) printf 'MongoDB 容器缺少 mongodump 或 mongorestore；聊天服务尚未停止。\n' ;;
    en:fix_tools_missing) printf 'The MongoDB container lacks mongodump or mongorestore; chat was not stopped.\n' ;;
    zh:fix_stopping) printf '正在停止聊天服务并备份 MongoDB...\n' ;;
    en:fix_stopping) printf 'Stopping chat and backing up MongoDB...\n' ;;
    zh:fix_writer_still_running) printf '聊天服务仍在运行，拒绝修改消息序号。\n' ;;
    en:fix_writer_still_running) printf 'Chat is still running; refusing to modify message sequences.\n' ;;
    zh:fix_incomplete) printf '发现上次未完成的修复：%s。请保留备份并联系维护人员，避免覆盖恢复点。\n' "$1" ;;
    en:fix_incomplete) printf 'Found an incomplete repair at %s. Preserve the backup and contact support before retrying.\n' "$1" ;;
    zh:fix_requires_running_chat) printf '修复前需要旧聊天服务处于运行状态；请先排查服务状态，避免错误版本的回退。\n' ;;
    en:fix_requires_running_chat) printf 'The old chat service must be running before repair; check its state to ensure safe rollback.\n' ;;
    zh:fix_backup_failed) printf '数据库备份失败，正在恢复旧聊天服务。\n' ;;
    en:fix_backup_failed) printf 'Database backup failed; restarting the old chat service.\n' ;;
    zh:fix_backup_complete) printf '数据库备份保存在：%s\n' "$1" ;;
    en:fix_backup_complete) printf 'Database backup saved at: %s\n' "$1" ;;
    zh:fix_restoring) printf '修复或验证失败，正在恢复聊天相关集合的备份...\n' ;;
    en:fix_restoring) printf 'Repair or validation failed; restoring the chat collections...\n' ;;
    zh:fix_restore_failed) printf '自动恢复失败。聊天服务保持停止；请保留备份 %s 并联系维护人员。\n' "$1" ;;
    en:fix_restore_failed) printf 'Automatic restore failed. Chat remains stopped; preserve backup %s and contact support.\n' "$1" ;;
    zh:fix_rolled_back) printf '已恢复备份并重新启动旧聊天服务。备份：%s\n' "$1" ;;
    en:fix_rolled_back) printf 'Backup restored and old chat service restarted. Backup: %s\n' "$1" ;;
    zh:fix_complete) printf '修复和验证成功。聊天服务保持停止，请立即执行 ./movo up 完成升级。\n' ;;
    en:fix_complete) printf 'Repair verified. Chat remains stopped; run ./movo up now to complete the upgrade.\n' ;;
    zh:fix_complete_build) printf '修复和验证成功。聊天服务保持停止，请立即执行 ./movo up --build 完成升级。\n' ;;
    en:fix_complete_build) printf 'Repair verified. Chat remains stopped; run ./movo up --build now to complete the upgrade.\n' ;;
    zh:pulling_images) printf '正在串行拉取镜像（第 %s 次）...\n' "$1" ;;
    en:pulling_images) printf 'Pulling images sequentially (attempt %s)...\n' "$1" ;;
    zh:pull_retry) printf '第 %s 次拉取失败，%s 秒后继续重试；按 Ctrl+C 可停止。\n' "$1" "$2" ;;
    en:pull_retry) printf 'Image pull attempt %s failed; retrying in %s seconds. Press Ctrl+C to stop.\n' "$1" "$2" ;;
    zh:backup_stopping) printf '正在停止服务并创建一致性数据卷备份...\n' ;;
    en:backup_stopping) printf 'Stopping services to create a consistent volume backup...\n' ;;
    zh:backup_failed) printf '备份失败，正在尝试恢复服务。\n' ;;
    en:backup_failed) printf 'Backup failed; attempting to restart the deployment.\n' ;;
    zh:backup_complete) printf '备份完成：%s\n' "$1" ;;
    en:backup_complete) printf 'Backup completed: %s\n' "$1" ;;
    zh:restore_confirm) printf '恢复会覆盖当前全部 MOVO 数据。确认后请重新执行并添加 --yes。\n' ;;
    en:restore_confirm) printf 'Restore overwrites all current MOVO data. Run the command again with --yes to confirm.\n' ;;
    zh:restore_complete) printf 'MOVO 数据恢复完成，服务已就绪。\n' ;;
    en:restore_complete) printf 'MOVO data restoration completed and services are ready.\n' ;;
    zh:dsh_build_target) printf 'DSH Runtime 目标构建版本：%s\n' "$1" ;;
    en:dsh_build_target) printf 'DSH Runtime build target: %s\n' "$1" ;;
    zh:dsh_running_version) printf 'DSH Runtime 运行版本：%s\n' "$1" ;;
    en:dsh_running_version) printf 'DSH Runtime running version: %s\n' "$1" ;;
    zh:migrating_project) printf '正在迁移旧版 Compose 服务名（数据卷会保留）...\n' ;;
    en:migrating_project) printf 'Migrating the legacy Compose project name (data volumes are preserved)...\n' ;;
    zh:remove_confirm) printf '这会永久删除全部 MOVO 数据卷和初始化数据。输入 MOVO 确认：' ;;
    en:remove_confirm) printf 'This permanently deletes all MOVO volumes and setup data. Type MOVO to confirm: ' ;;
    zh:remove_aborted) printf '已取消。自动化环境请显式传入 ./movo down -v --yes。\n' ;;
    en:remove_aborted) printf 'Cancelled. For automation, explicitly pass ./movo down -v --yes.\n' ;;
    zh:start_failed) printf '\nMOVO 启动失败，当前服务状态如下：\n' ;;
    en:start_failed) printf '\nMOVO failed to start. Current service status:\n' ;;
    zh:logs_hint) printf '请执行 ./movo logs 查看详细日志。\n' ;;
    en:logs_hint) printf 'Run ./movo logs for detailed logs.\n' ;;
    zh:stopped) printf 'MOVO 已停止，数据卷仍然保留。\n' ;;
    en:stopped) printf 'MOVO has stopped. Data volumes are preserved.\n' ;;
    zh:stopped_removed) printf 'MOVO 已停止，数据卷和初始化数据已删除。\n' ;;
    en:stopped_removed) printf 'MOVO has stopped. Data volumes and setup state were deleted.\n' ;;
    zh:unknown) printf '未知命令：%s\n\n' "$1" ;;
    en:unknown) printf 'Unknown command: %s\n\n' "$1" ;;
    *) printf '%s' "${key}" ;;
  esac
}
