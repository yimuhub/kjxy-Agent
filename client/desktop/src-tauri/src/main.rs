// 桌面壳二进制入口：逻辑全部在 lib.rs（移动端复用同一 run()）
#[cfg(not(debug_assertions))]
fn main() {
    techyard_desktop_lib::run();
}

// dev 构建同路径；保留双分支便于将来 dev/release 行为分叉注释
#[cfg(debug_assertions)]
fn main() {
    techyard_desktop_lib::run();
}
