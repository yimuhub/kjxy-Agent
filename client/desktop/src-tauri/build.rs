fn main() {
    // 主窗体的页面来自本机引擎（http://127.0.0.1:187xx），属于 Tauri 的 remote origin：
    // 自定义命令必须声明 app ACL manifest，capabilities 里才可能放行。
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(
            tauri_build::AppManifest::new().commands(&[
                "save_export",
                "open_mic_settings",
                "startup_status",
                "open_startup_log",
                "quit_app",
            ]),
        ),
    )
    .expect("tauri-build 失败");
}
