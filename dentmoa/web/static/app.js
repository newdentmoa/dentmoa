/* 덴트모아 대시보드 — 작은 편의 기능 (자바스크립트가 꺼져 있어도 화면은 동작한다) */
(function () {
  "use strict";

  function $all(sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  }

  // 넓은 화면에서는 검색 창을 펼쳐 둔다
  function autoOpen() {
    if (!window.matchMedia("(min-width: 720px)").matches) return;
    $all("details[data-auto-open]").forEach(function (d) { d.open = true; });
  }

  // '전체 선택' / '전체 해제' 버튼
  function checkAllButtons() {
    document.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-check-all], [data-uncheck-all]");
      if (!btn) return;
      var name = btn.getAttribute("data-check-all") || btn.getAttribute("data-uncheck-all");
      var on = btn.hasAttribute("data-check-all");
      $all('input[type="checkbox"][name="' + name + '"]').forEach(function (box) {
        if (!box.disabled) box.checked = on;
      });
    });
  }

  // 지역 고르기: 전국 → 나머지 끄기, 시·도 전체 → 그 아래 구·시·군 끄기
  function regionPicker() {
    var root = document.querySelector("[data-region-picker]");
    if (!root) return;
    var all = root.querySelector("[data-region-all]");
    var summary = root.querySelector("[data-region-summary]");

    function refresh() {
      var nationwide = all && all.checked;
      $all("[data-sido-all]", root).forEach(function (sidoBox) {
        var sido = sidoBox.getAttribute("data-sido-all");
        sidoBox.disabled = nationwide;
        if (nationwide) sidoBox.checked = false;
        var whole = nationwide || sidoBox.checked;
        var n = 0;
        $all('[data-sigungu-of="' + sido + '"]', root).forEach(function (box) {
          box.disabled = whole;
          if (whole) box.checked = false;
          if (box.checked) n += 1;
        });
        var wrap = sidoBox.closest("[data-sido]");
        var count = wrap && wrap.querySelector("[data-count]");
        if (count) count.textContent = sidoBox.checked ? "전체" : (n ? n + "곳" : "");
      });
      if (summary) {
        var picked = $all('input[name="regions"]:checked', root).map(function (b) {
          return b.value === "전국" ? "전국" : (b.hasAttribute("data-sido-all") ? b.value + " 전체" : b.value);
        });
        if (nationwide) summary.textContent = "선택: 전국 — 특정 지역만 받으려면 '전국'을 먼저 끄세요.";
        else summary.textContent = picked.length ? "선택: " + picked.join(", ") : "아직 고른 지역이 없어요.";
      }
    }

    root.addEventListener("change", function (e) {
      if (e.target.name === "regions") refresh();
    });
    refresh();
  }

  // 관심(★)·숨기기 버튼: 화면을 새로 고치지 않고 바로 바꾼다
  function toggleForms() {
    document.addEventListener("submit", function (e) {
      var form = e.target.closest("form[data-toggle]");
      if (!form || !window.fetch || !window.FormData) return;
      e.preventDefault();
      var kind = form.getAttribute("data-toggle");
      var btn = form.querySelector("button");
      if (btn) btn.disabled = true;
      fetch(form.action, {
        method: "POST",
        body: new FormData(form),
        headers: { "X-Requested-With": "fetch" },
        credentials: "same-origin"
      })
        .then(function (r) {
          return r.json().then(function (data) {
            if (!r.ok || !data.ok) throw new Error(data.error || "처리하지 못했어요.");
            return data;
          });
        })
        .then(function (data) {
          var label = btn && btn.querySelector("[data-label]");
          if (btn) btn.setAttribute("aria-pressed", data.value ? "true" : "false");
          if (kind === "star") {
            if (btn) btn.classList.toggle("on", data.value);
            if (label) label.textContent = data.value ? "★ 관심" : "☆ 관심";
          } else {
            if (label) label.textContent = data.value ? "숨김 취소" : "숨기기";
            var card = form.closest(".posting");
            if (card) card.classList.toggle("is-hidden", data.value);
          }
        })
        .catch(function (err) {
          window.alert(err.message || "처리하지 못했어요. 페이지를 새로고침해 주세요.");
        })
        .then(function () {
          if (btn) btn.disabled = false;
        });
    });
  }

  // 오래 걸리는 버튼: 누르면 '…하는 중'으로 바꾸고 두 번 눌리지 않게
  function busyForms() {
    document.addEventListener("submit", function (e) {
      var form = e.target;
      if (!form.hasAttribute || !form.hasAttribute("data-busy") || e.defaultPrevented) return;
      var text = form.getAttribute("data-busy") || "처리 중…";
      var btn = form.querySelector('button[type="submit"], button:not([type])');
      if (!btn) return;
      setTimeout(function () {
        btn.setAttribute("data-original", btn.textContent);
        btn.disabled = true;
        btn.textContent = text;
      }, 0);
    });
    // 뒤로 가기로 돌아왔을 때 버튼 되살리기
    window.addEventListener("pageshow", function (e) {
      if (!e.persisted) return;
      $all("button[data-original]").forEach(function (btn) {
        btn.disabled = false;
        btn.textContent = btn.getAttribute("data-original");
        btn.removeAttribute("data-original");
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    autoOpen();
    checkAllButtons();
    regionPicker();
    toggleForms();
    busyForms();
  });
})();
