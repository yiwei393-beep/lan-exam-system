/* ============================================
   i18n 核心模块（非 module / 同步）
   - 默认中文（zh-CN），支持英文（en-US）
   - 持久化：localStorage("locale")
   - 提供 window.t(key, vars) 与 window.i18n
   - 依赖：window.I18N_ZH_CN、window.I18N_EN_US 由词条脚本提前注入
   ============================================ */
(function (root) {
  const SUPPORTED = ["zh-CN", "en-US"];
  const DEFAULT_LOCALE = "zh-CN";
  const DICTS = {
    "zh-CN": root.I18N_ZH_CN || {},
    "en-US": root.I18N_EN_US || {},
  };

  const listeners = new Set();
  let initialized = false;

  const I18n = {
    locale: DEFAULT_LOCALE,
    dict: {},
    fallback: DICTS["zh-CN"],

    init() {
      if (initialized) return this;
      try {
        const stored = root.localStorage.getItem("locale");
        if (stored && SUPPORTED.includes(stored)) {
          this.locale = stored;
        }
      } catch (e) { /* 忽略 */ }
      this.dict = DICTS[this.locale] || DICTS[DEFAULT_LOCALE];
      this.fallback = DICTS[DEFAULT_LOCALE];
      initialized = true;
      return this;
    },

    setLocale(locale) {
      if (!SUPPORTED.includes(locale)) locale = DEFAULT_LOCALE;
      this.locale = locale;
      try { root.localStorage.setItem("locale", locale); } catch (e) { /* 忽略 */ }
      this.dict = DICTS[locale] || DICTS[DEFAULT_LOCALE];
      listeners.forEach((fn) => { try { fn(locale); } catch (e) { /* 忽略 */ } });
      try { root.dispatchEvent(new CustomEvent("localechange", { detail: { locale } })); } catch (e) { /* 忽略 */ }
    },

    t(key, vars) {
      const dict = this.dict || {};
      const fb = this.fallback || {};
      let str = dict[key] || fb[key] || key;
      if (vars && typeof str === "string") {
        Object.keys(vars).forEach((k) => {
          str = str.replace(new RegExp(`\\{${k}\\}`, "g"), vars[k]);
        });
      }
      return str;
    },

    onChange(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },

    supported: SUPPORTED,
  };

  // 同步初始化（依赖脚本已通过 defer 顺序加载）
  I18n.init();

  root.i18n = I18n;
  root.t = (key, vars) => I18n.t(key, vars);
})(window);
