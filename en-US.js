<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <title>i18n Test</title>
  </head>
  <body>
    <div id="r"></div>
    <script src="/i18n/zh-CN.js"></script>
    <script src="/i18n/en-US.js"></script>
    <script src="/i18n/index.js"></script>
    <script>
      const out = [];
      out.push("Default locale: " + i18n.locale);
      out.push("t(nav.dashboard) zh-CN: " + t("nav.dashboard"));
      i18n.setLocale("en-US");
      out.push("After setLocale(en-US): locale=" + i18n.locale);
      out.push("t(nav.dashboard) en-US: " + t("nav.dashboard"));
      out.push("t(nav.students) en-US: " + t("nav.students"));
      out.push("t(nav.exam) en-US: " + t("nav.exam"));
      out.push("t(login.welcome) en-US: " + t("login.welcome"));
      out.push("dict size: " + Object.keys(i18n.dict).length);
      out.push("fallback size: " + Object.keys(i18n.fallback || {}).length);
      document.getElementById("r").innerText = out.join("\n");
    </script>
  </body>
</html>
