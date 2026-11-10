/* 启动页：轮询 Rust 侧的真实阶段（sidecar 日志 + 健康探针），不靠前端猜时间。 */
(function () {
  var TOTAL = 6;
  var headline = document.getElementById("headline");
  var detail = document.getElementById("detail");
  var bar = document.getElementById("bar");
  var progress = document.querySelector(".bar");
  var elapsedEl = document.getElementById("elapsed");
  var note = document.getElementById("note");
  var logBtn = document.getElementById("open-log");
  var quitBtn = document.getElementById("quit");
  var items = Array.prototype.slice.call(document.querySelectorAll("#steps li"));

  var baseElapsed = 0;
  var baseAt = Date.now();
  var lastStatus = null;
  var failedOnce = false;

  function invoke(cmd) {
    if (!window.__TAURI__ || !window.__TAURI__.core) {
      return Promise.reject(new Error("当前不在桌面客户端内"));
    }
    return window.__TAURI__.core.invoke(cmd);
  }

  function paint(status) {
    lastStatus = status;
    baseElapsed = status.elapsed_secs || 0;
    baseAt = Date.now();

    var failed = !!status.failed;
    var ready = !!status.ready;
    var stage = Math.max(0, Math.min(TOTAL - 1, status.stage || 0));

    items.forEach(function (item, index) {
      var state = "";
      if (failed && index === stage && !ready) state = "failed";
      else if (ready || index < stage) state = "done";
      else if (index === stage) state = "active";
      item.className = state;
      item.querySelector(".ico").textContent =
        state === "done" ? "✓" : state === "failed" ? "!" : "";
    });

    var percent = Math.max(2, Math.min(100, status.percent || 0));
    bar.style.width = percent + "%";
    progress.setAttribute("aria-valuenow", String(percent));

    if (failed) {
      headline.textContent = "启动没有完成";
      detail.textContent = status.failed;
      detail.className = "detail error";
      note.textContent = "可查看启动日志定位原因；退出后重开通常可恢复。";
      if (!failedOnce) {
        failedOnce = true;
        logBtn.textContent = "查看启动日志";
      }
    } else if (ready) {
      headline.textContent = "启动完成，正在进入主界面…";
      detail.textContent = status.detail || "已就绪";
      detail.className = "detail";
      note.textContent = "主界面即将出现。";
    } else {
      headline.textContent =
        (status.elapsed_secs || 0) >= 120 ? "仍在启动，请再等一会儿…" : "正在启动本地引擎…";
      detail.textContent = status.detail || "正在准备本机环境…";
      detail.className = "detail";
      note.textContent = "请勿强制关闭，正在准备本机数据。";
    }
  }

  function tickElapsed() {
    var extra = Math.floor((Date.now() - baseAt) / 1000);
    elapsedEl.textContent = String(baseElapsed + Math.max(0, extra));
  }

  function poll() {
    invoke("startup_status")
      .then(paint)
      .catch(function (err) {
        // 命令尚未注册/页面在浏览器打开：给出可理解的兜底，不假装在启动
        if (!lastStatus) {
          headline.textContent = "请从「科技小院」桌面客户端打开";
          detail.textContent = String((err && err.message) || err);
        }
      })
      .then(function () {
        window.setTimeout(poll, 600);
      });
  }

  logBtn.addEventListener("click", function () {
    logBtn.disabled = true;
    invoke("open_startup_log")
      .then(function () {
        logBtn.textContent = "已打开日志";
      })
      .catch(function (err) {
        logBtn.textContent = "查看启动日志";
        detail.textContent = "打开日志失败：" + String((err && err.message) || err);
        detail.className = "detail error";
      })
      .then(function () {
        window.setTimeout(function () {
          logBtn.disabled = false;
        }, 1200);
      });
  });

  quitBtn.addEventListener("click", function () {
    invoke("quit_app").catch(function () {
      window.close();
    });
  });

  window.setInterval(tickElapsed, 500);
  poll();
})();
