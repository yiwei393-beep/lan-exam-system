const state = {
  role: localStorage.getItem("role"),
  token: localStorage.getItem("token"),
  student: JSON.parse(localStorage.getItem("student") || "null"),
  teacherTab: "dashboard",
  examData: null,
  answers: {},
  currentQuestion: 0,
  saveTimer: null,
  monitorTimer: null,
  chartInstances: [],
};

const $app = document.querySelector("#app");

/* ============================================
   自定义弹窗工具 (替代浏览器 confirm/alert)
   ============================================ */
function showModal({ icon = "&#x26A0;&#xFE0F;", title, message, confirmText = "确定", cancelText = "取消", danger = false, onConfirm, onCancel }) {
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.innerHTML = html`
    <div class="modal">
      <div class="modal-icon ${danger ? 'danger' : ''}">${icon}</div>
      <h3>${title}</h3>
      <p>${message}</p>
      <div class="modal-actions">
        <button class="btn-outline" data-act="cancel">${cancelText}</button>
        <button class="${danger ? 'btn-danger' : 'btn-primary'}" data-act="confirm">${confirmText}</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
  const close = (act) => {
    overlay.remove();
    if (act === "confirm" && onConfirm) onConfirm();
    if (act === "cancel" && onCancel) onCancel();
  };
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) close("cancel");
  });
  overlay.querySelector('[data-act="cancel"]').addEventListener("click", () => close("cancel"));
  overlay.querySelector('[data-act="confirm"]').addEventListener("click", () => close("confirm"));
}

function showToast(message, type = "info") {
  const old = document.querySelector(".toast");
  if (old) old.remove();
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  const icon = type === "success" ? "&#x2713;" : type === "error" ? "&#x2715;" : "&#x2139;&#xFE0F;";
  toast.innerHTML = `<span style="font-size:16px">${icon}</span><span>${message}</span>`;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 2400);
}

function clearCharts() {
  state.chartInstances.forEach((c) => c.dispose());
  state.chartInstances = [];
}

function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (!(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  return fetch(path, { ...options, headers }).then(async (res) => {
    if (path.includes("/export")) return res;
    const body = await res.json();
    if (!res.ok || body.code !== 200) throw new Error(body.message || "请求失败");
    return body.data;
  });
}

function html(strings, ...values) {
  return strings.reduce((out, s, i) => out + s + (values[i] ?? ""), "");
}

function setSession(role, token, student = null) {
  state.role = role;
  state.token = token;
  state.student = student;
  localStorage.setItem("role", role);
  localStorage.setItem("token", token);
  if (student) localStorage.setItem("student", JSON.stringify(student));
}

function logout() {
  clearCharts();
  localStorage.clear();
  Object.assign(state, { role: null, token: null, student: null, examData: null, answers: {} });
  clearInterval(state.saveTimer);
  clearInterval(state.monitorTimer);
  clearInterval(state.countdown);
  renderHome();
}

/* ============================================
   首页 - 选择登录角色
   ============================================ */
function renderHome() {
  if (state.role === "teacher" && state.token) return renderTeacher();
  if (state.role === "student" && state.token) return renderStudentGate();
  $app.innerHTML = html`
    <section class="login-wrap">
      <div class="login-card">
        <div class="login-logo">
          <span class="login-logo-text">LAN<br/>EXAM</span>
        </div>
        <h1>局域网考试系统</h1>
        <p class="subtitle">请选择您的登录身份</p>
        <button class="btn-primary" onclick="renderStudentLogin()">学生登录</button>
        <button class="btn-outline" onclick="renderTeacherLogin()">教师登录</button>
        <p class="login-hint">学生账号为学号，密码为学号后5位<br/>教师默认账号 admin / admin123</p>
      </div>
    </section>
  `;
}

/* ============================================
   学生登录页 - 左右分栏设计
   ============================================ */
function renderStudentLogin() {
  $app.innerHTML = html`
    <section class="login-wrap">
      <div class="login-split">
        <div class="login-branding">
          <h2>局域网考试系统</h2>
          <p class="en-title">LAN Exam System</p>
          <p class="tagline">安全 · 稳定 · 高效</p>
          <div class="features">
            <div class="feature-icon">&#x1F4D3;</div>
            <div class="feature-icon">&#x23F0;</div>
            <div class="feature-icon">&#x1F4CA;</div>
          </div>
        </div>
        <div class="login-form-side">
          <form onsubmit="studentLogin(event)">
            <h1>学生登录</h1>
            <p class="subtitle">请输入学号和密码进行登录</p>
            <div class="field">
              <label>学号</label>
              <input name="student_id" placeholder="请输入学号" required />
            </div>
            <div class="field">
              <label>密码</label>
              <input name="password" type="password" placeholder="请输入密码（学号后5位）" required />
            </div>
            <p class="password-hint">初始密码为学号后5位</p>
            <button type="submit" class="btn-primary">登 录</button>
            <p class="footer-hint">如遇问题请联系监考老师</p>
            <div id="msg" class="msg"></div>
          </form>
        </div>
      </div>
    </section>
  `;
}

async function studentLogin(event) {
  event.preventDefault();
  const form = new FormData(event.target);
  try {
    const data = await api("/api/student/login", {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(form)),
    });
    setSession("student", data.token, data.student);
    await checkAndResetSubmittedExam();
    renderStudentGate();
  } catch (err) {
    document.querySelector("#msg").textContent = err.message;
  }
}

async function checkAndResetSubmittedExam() {
  try {
    const data = await api("/api/exam/status");
    const status = data && data.record && data.record.status;
    if (status === "submitted" || status === "forced") {
      await api("/api/student/reset-exam", { method: "POST", body: JSON.stringify({}) });
      showToast("已开启新一轮答题，原有答卷已重置", "info");
    }
  } catch (err) {
  }
}

/* ============================================
   教师登录页 - 居中卡片设计
   ============================================ */
function renderTeacherLogin() {
  $app.innerHTML = html`
    <section class="login-wrap">
      <form class="login-card" onsubmit="teacherLogin(event)">
        <div class="login-logo">
          <span class="login-logo-text">LAN<br/>EXAM</span>
        </div>
        <h1>教师登录</h1>
        <p class="subtitle">局域网考试管理系统</p>
        <div class="field">
          <label>用户名</label>
          <input name="username" placeholder="请输入用户名" value="admin" required />
        </div>
        <div class="field">
          <label>密码</label>
          <input name="password" type="password" placeholder="请输入密码" value="admin123" required />
        </div>
        <button type="submit" class="btn-primary">登 录</button>
        <button type="button" class="btn-outline" onclick="renderHome()">返回</button>
        <div id="msg" class="msg"></div>
      </form>
    </section>
  `;
}

async function teacherLogin(event) {
  event.preventDefault();
  try {
    const data = await api("/api/teacher/login", {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(new FormData(event.target))),
    });
    setSession("teacher", data.token);
    renderTeacher();
  } catch (err) {
    document.querySelector("#msg").textContent = err.message;
  }
}

/* ============================================
   姓名确认页
   ============================================ */
function renderStudentGate() {
  $app.innerHTML = html`
    <section class="login-wrap">
      <div class="confirm-card">
        <div class="confirm-icon">&#x1F464;</div>
        <h1>请确认您的姓名</h1>
        <p class="desc">系统识别到您的姓名为：</p>
        <div class="name-display">
          <span class="name">${state.student.name || '未知'}</span>
        </div>
        <form onsubmit="confirmName(event)">
          <input type="hidden" name="name" value="${state.student.name || ''}" />
          <div class="confirm-actions">
            <button type="submit" class="btn-primary">确认无误</button>
            <button type="button" class="btn-outline-danger" onclick="logout()">信息有误</button>
          </div>
          <div id="msg" class="msg"></div>
        </form>
      </div>
    </section>
  `;
}

async function confirmName(event) {
  event.preventDefault();
  try {
    await api("/api/student/confirm-name", {
      method: "POST",
      body: JSON.stringify(Object.fromEntries(new FormData(event.target))),
    });
    renderInstructions();
  } catch (err) {
    document.querySelector("#msg").textContent = err.message;
  }
}

/* ============================================
   考试须知页
   ============================================ */
async function renderInstructions() {
  const data = await api("/api/exam/current");
  const questionTypes = "单选题、多选题、判断题、简答题";
  $app.innerHTML = html`
    <section class="login-wrap">
      <div class="instructions-card">
        <div class="instructions-header">
          <h1>&#x1F4CB; 考试须知</h1>
        </div>
        <div class="instructions-body">
          <div class="exam-info-grid">
            <div class="exam-info-item">
              <span class="label">考试名称</span>
              <span class="value">${data.exam.name}</span>
            </div>
            <div class="exam-info-item">
              <span class="label">考试时长</span>
              <span class="value">${data.exam.duration_minutes} 分钟</span>
            </div>
            <div class="exam-info-item">
              <span class="label">试卷总分</span>
              <span class="value">${data.paper.total_score} 分</span>
            </div>
            <div class="exam-info-item">
              <span class="label">题目数量</span>
              <span class="value">${data.paper.total_score > 0 ? '见试卷' : '0'} 题 (${questionTypes})</span>
            </div>
          </div>
          <div class="instructions-divider"></div>
          <div class="instructions-rules">
            <h3>&#x26A0;&#xFE0F; 注意事项</h3>
            <ul class="rule-list">
              <li>考试开始后，系统将自动倒计时，时间到自动提交试卷</li>
              <li>系统每30秒自动保存答题进度，意外关闭浏览器可恢复</li>
              <li>答题过程中请勿切换浏览器标签页或最小化窗口</li>
              <li>最后5分钟系统将提示剩余时间，请合理安排答题</li>
              <li>提交试卷前请仔细检查，提交后不可修改</li>
            </ul>
          </div>
          <label class="checkbox-agree">
            <input id="agree" type="checkbox" /> 我已阅读考试须知
          </label>
          <div class="instructions-actions">
            <button class="btn-primary" onclick="startStudentExam()">开始考试</button>
          </div>
          <div id="msg" class="msg"></div>
        </div>
      </div>
    </section>
  `;
}

async function startStudentExam() {
  if (!document.querySelector("#agree").checked) {
    document.querySelector("#msg").textContent = "请先勾选已阅读考试须知";
    return;
  }
  const data = await api("/api/exam/paper");
  state.examData = data;
  state.answers = data.record.answers || {};
  renderExam();
  state.saveTimer = setInterval(autoSave, 30000);
  window.addEventListener("beforeunload", autoSave);
}

function isAnswered(question) {
  return String(state.answers[question.question_id] || "").trim() !== "";
}

function answeredCount() {
  return state.examData.questions.filter(isAnswered).length;
}

/* ============================================
   答题页面 - 核心页面
   ============================================ */
function renderExam() {
  const { exam, questions, record } = state.examData;
  const start = record.start_time ? new Date(record.start_time).getTime() : Date.now();
  const end = start + exam.duration_minutes * 60 * 1000;
  const percent = Math.round((answeredCount() / questions.length) * 100);
  $app.innerHTML = html`
    <div class="shell">
      <div class="exam-top">
        <span class="exam-title">${exam.name}</span>
        <div class="timer-wrap" id="timer-wrap">
          <span class="timer-icon">&#x23F0;</span>
          <span id="timer" class="timer">--:--:--</span>
        </div>
        <span class="student-info">考生: ${state.student.name} (${state.student.student_id})</span>
        <button class="btn-danger" onclick="submitExam(false)">交 卷</button>
      </div>
      <div class="exam-layout">
        <aside class="question-nav">
          <h3>题目导航</h3>
          <div class="q-buttons">
            ${questions
              .map(
                (q, i) =>
                  `<button class="qbtn ${isAnswered(q) ? "done" : ""} ${i === state.currentQuestion ? "current" : ""}" onclick="jumpQuestion(${i})">${q.question_no}</button>`
              )
              .join("")}
          </div>
          <div class="nav-legend">
            <div class="legend-item"><span class="legend-dot done"></span>已作答</div>
            <div class="legend-item"><span class="legend-dot unanswered"></span>未作答</div>
            <div class="legend-item"><span class="legend-dot current"></span>当前题目</div>
          </div>
          <div class="progress-section">
            <p class="progress-label">答题进度</p>
            <div class="progress"><span style="width:${percent}%"></span></div>
            <p class="progress-text">${answeredCount()} / ${questions.length}</p>
          </div>
        </aside>
        <main class="question-area">
          ${questions.map((q, i) => renderQuestion(q, i)).join("")}
        </main>
      </div>
    </div>
  `;
  updateTimer(end);
  state.countdown = setInterval(() => updateTimer(end), 1000);
}

function renderQuestion(q, index) {
  const label = { single_choice: "单选题", multiple_choice: "多选题", true_false: "判断题", short_answer: "简答题" }[q.type];
  return html`
    <section id="q${index}" class="panel question">
      <div class="question-header">
        <span class="question-no">第 ${q.question_no} 题</span>
        <span class="question-type-tag">${label}</span>
        <span class="question-score">${q.score}分</span>
      </div>
      <p class="question-content">${q.content}</p>
      ${renderAnswerControl(q)}
      <div class="question-actions">
        <button class="btn-outline" onclick="jumpQuestion(${Math.max(0, index - 1)})">&#x2190; 上一题</button>
        <button class="btn-primary" onclick="jumpQuestion(${Math.min(state.examData.questions.length - 1, index + 1)})">下一题 &#x2192;</button>
        <span class="save-status">&#x2713; 已自动保存</span>
      </div>
    </section>
  `;
}

function renderAnswerControl(q) {
  const current = state.answers[q.question_id] || "";
  if (q.type === "short_answer") {
    return `<textarea oninput="setAnswer('${q.question_id}', this.value)" placeholder="请输入您的答案...">${current}</textarea>`;
  }
  if (q.type === "true_false") {
    return `
      <label class="option ${current === 'true' ? 'selected' : ''}"><input type="radio" name="q${q.question_id}" ${current === "true" ? "checked" : ""} onchange="setAnswer('${q.question_id}', 'true')" /> <span>正确</span></label>
      <label class="option ${current === 'false' ? 'selected' : ''}"><input type="radio" name="q${q.question_id}" ${current === "false" ? "checked" : ""} onchange="setAnswer('${q.question_id}', 'false')" /> <span>错误</span></label>
    `;
  }
  const multiple = q.type === "multiple_choice";
  const selected = current ? current.split(",") : [];
  return q.options
    .map(
      (opt) => `
      <label class="option ${selected.includes(opt.key) ? 'selected' : ''}">
        <input type="${multiple ? "checkbox" : "radio"}" name="q${q.question_id}" value="${opt.key}" ${selected.includes(opt.key) ? "checked" : ""} onchange="setChoice('${q.question_id}', '${q.type}')" />
        <span>${opt.key}. ${opt.value}</span>
      </label>`
    )
    .join("");
}

function setAnswer(questionId, value) {
  state.answers[questionId] = value;
  refreshNavOnly();
}

function setChoice(questionId, type) {
  const inputs = [...document.querySelectorAll(`[name="q${questionId}"]:checked`)];
  state.answers[questionId] = type === "multiple_choice" ? inputs.map((i) => i.value).sort().join(",") : inputs[0]?.value || "";
  refreshNavOnly();
}

function refreshNavOnly() {
  document.querySelectorAll(".qbtn").forEach((btn, i) => {
    btn.classList.toggle("done", isAnswered(state.examData.questions[i]));
  });
  const percent = Math.round((answeredCount() / state.examData.questions.length) * 100);
  document.querySelector(".progress span").style.width = `${percent}%`;
  document.querySelector(".progress-text").textContent = `${answeredCount()} / ${state.examData.questions.length}`;
}

function jumpQuestion(index) {
  state.currentQuestion = index;
  document.querySelector(`#q${index}`).scrollIntoView({ behavior: "smooth", block: "start" });
  document.querySelectorAll(".qbtn").forEach((btn, i) => btn.classList.toggle("current", i === index));
}

function updateTimer(end) {
  const left = Math.max(0, end - Date.now());
  const total = Math.floor(left / 1000);
  const h = String(Math.floor(total / 3600)).padStart(2, "0");
  const m = String(Math.floor((total % 3600) / 60)).padStart(2, "0");
  const s = String(total % 60).padStart(2, "0");
  const timer = document.querySelector("#timer");
  const timerWrap = document.querySelector("#timer-wrap");
  if (!timer) return;
  timer.textContent = `${h}:${m}:${s}`;
  const isDanger = total <= 300;
  timerWrap.classList.toggle("timer-wrap", !isDanger);
  timerWrap.style.background = isDanger ? '#FEF0F0' : '';
  timerWrap.style.borderColor = isDanger ? '#FDE2E2' : '';
  timer.classList.toggle("danger-time", isDanger);
  if (left <= 0) submitExam(true);
}

async function autoSave() {
  if (!state.examData) return;
  await api("/api/exam/auto-save", { method: "POST", body: JSON.stringify({ answers: state.answers }) }).catch(() => {});
}

async function submitExam(auto) {
  if (!auto) {
    const total = state.examData.questions.length;
    const left = total - answeredCount();
    if (left === 0) {
      // 全部答完：直接提交，使用toast提示
      await doSubmit();
      showToast("已全部作答，正在提交答卷...", "success");
      return;
    }
    // 有未答题：使用自定义弹窗
    showModal({
      icon: "&#x26A0;&#xFE0F;",
      title: "确认交卷",
      message: `您还有 <strong style="color:var(--danger)">${left}</strong> 道题未作答（${answeredCount()}/${total}），确认要交卷吗？`,
      confirmText: "确认交卷",
      cancelText: "继续答题",
      onConfirm: async () => {
        await doSubmit();
      },
    });
    return;
  }
  await doSubmit();
}

async function doSubmit() {
  clearInterval(state.saveTimer);
  clearInterval(state.countdown);
  await api("/api/exam/submit", { method: "POST", body: JSON.stringify({ answers: state.answers }) });
  renderSubmitted();
}

/* ============================================
   提交成功页
   ============================================ */
function renderSubmitted() {
  const now = new Date();
  const timeStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')} ${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;
  const examName = state.examData ? state.examData.exam.name : '考试';
  $app.innerHTML = html`
    <section class="login-wrap">
      <div class="success-card">
        <div class="success-icon">&#x2713;</div>
        <h1>试卷提交成功</h1>
        <p class="desc">您的答卷已成功提交，请耐心等待成绩公布</p>
        <div class="success-info">
          <div class="info-row">
            <span class="info-label">考试名称</span>
            <span class="info-value">${examName}</span>
          </div>
          <div class="info-row">
            <span class="info-label">提交时间</span>
            <span class="info-value">${timeStr}</span>
          </div>
        </div>
        <button class="btn-primary" onclick="logout()">关闭页面</button>
      </div>
    </section>
  `;
}

/* ============================================
   教师端布局
   ============================================ */
function renderTeacher() {
  clearInterval(state.monitorTimer);
  clearCharts();
  const tabs = [
    ["dashboard", "&#x1F4CA;", "仪表盘"],
    ["students", "&#x1F465;", "学生管理"],
    ["papers", "&#x1F4D3;", "试卷管理"],
    ["exam", "&#x1F4DD;", "考试管理"],
    ["monitor", "&#x1F4F9;", "实时监控"],
    ["grading", "&#x270D;", "批改评分"],
    ["results", "&#x1F4CB;", "成绩管理"],
  ];
  $app.innerHTML = html`
    <div class="shell">
      <header class="topbar">
        <span class="brand">
          <span class="brand-logo">LAN<br/>EXAM</span>
          局域网考试系统教师端
        </span>
        <span class="topbar-right">
          <span>欢迎, 管理员</span>
          <button class="btn-outline btn-sm" onclick="logout()">退出登录</button>
        </span>
      </header>
      <div class="layout">
        <aside class="sidebar">
          <div class="sidebar-logo"><span>LAN Exam System</span></div>
          ${tabs.map(([id, icon, name]) => `<button class="sidebtn ${state.teacherTab === id ? "active" : ""}" onclick="setTeacherTab('${id}')">${icon}  ${name}</button>`).join("")}
        </aside>
        <main id="teacher-main" class="main"></main>
      </div>
    </div>
  `;
  renderTeacherTab();
}

function setTeacherTab(tab) {
  state.teacherTab = tab;
  renderTeacher();
}

async function renderTeacherTab() {
  const main = document.querySelector("#teacher-main");
  if (state.teacherTab === "dashboard") return renderDashboard(main);
  if (state.teacherTab === "students") return renderStudents(main);
  if (state.teacherTab === "papers") return renderPapers(main);
  if (state.teacherTab === "exam") return renderExamAdmin(main);
  if (state.teacherTab === "monitor") return renderMonitor(main);
  if (state.teacherTab === "grading") return renderGrading(main);
  if (state.teacherTab === "results") return renderResults(main);
}

/* ============================================
   仪表盘 - ECharts 数据可视化
   ============================================ */
async function renderDashboard(main) {
  clearCharts();
  const [exam, monitor, dashboard] = await Promise.all([
    api("/api/exam/current"),
    api("/api/teacher/monitor"),
    api("/api/teacher/dashboard"),
  ]);
  const statusText = statusName(exam.exam.status);
  main.innerHTML = html`
    <div class="page-header">
      <h2>仪表盘</h2>
      <span class="hint">${exam.exam.name} · ${statusText}</span>
    </div>
    <div class="dashboard-summary">
      <div class="stat-card" style="--stat-color: #4A90D9">
        <div class="stat-label">学生总数</div>
        <div class="stat-value">${dashboard.totals.students}</div>
        <div class="stat-extra">已登录 ${monitor.stats.logged_in} 人</div>
      </div>
      <div class="stat-card" style="--stat-color: #67C23A">
        <div class="stat-label">答题中</div>
        <div class="stat-value">${monitor.stats.answering}</div>
        <div class="stat-extra">实时进行</div>
      </div>
      <div class="stat-card" style="--stat-color: #E6A23C">
        <div class="stat-label">已提交</div>
        <div class="stat-value">${monitor.stats.submitted}</div>
        <div class="stat-extra">含强制收卷</div>
      </div>
      <div class="stat-card" style="--stat-color: #F56C6C">
        <div class="stat-label">题目总数</div>
        <div class="stat-value">${dashboard.totals.questions}</div>
      </div>
    </div>
    <div class="chart-grid">
      <div class="chart-card">
        <div class="chart-header">
          <span class="chart-title">&#x1F4CA; 学生状态分布</span>
          <span class="chart-desc">实时</span>
        </div>
        <div id="chart-status" class="chart-container"></div>
      </div>
      <div class="chart-card">
        <div class="chart-header">
          <span class="chart-title">&#x1F4DD; 答题进度分布</span>
          <span class="chart-desc">实时</span>
        </div>
        <div id="chart-progress" class="chart-container"></div>
      </div>
    </div>
    <div class="chart-card">
      <div class="chart-header">
        <span class="chart-title">&#x1F4C8; 成绩分数段分布</span>
        <span class="chart-desc">${exam.exam.name}</span>
      </div>
      <div id="chart-score" class="chart-container chart-large"></div>
    </div>
  `;
  renderStatusChart(dashboard.status_distribution);
  renderProgressChart(dashboard.answered_distribution);
  renderScoreChart(dashboard.score_distribution);

  // 自适应窗口大小
  if (!state._resizeHandler) {
    state._resizeHandler = () => state.chartInstances.forEach((c) => c && c.resize && c.resize());
    window.addEventListener("resize", state._resizeHandler);
  }
}

function renderStatusChart(data) {
  const el = document.querySelector("#chart-status");
  if (!el) return;
  const chart = echarts.init(el);
  const colorMap = {
    "未登录": "#909399",
    "已登录": "#E6A23C",
    "答题中": "#67C23A",
    "已提交": "#4A90D9",
    "已强制收卷": "#F56C6C",
  };
  const items = Object.keys(data).map((k) => ({ name: k, value: data[k], itemStyle: { color: colorMap[k] } }));
  chart.setOption({
    tooltip: { trigger: "item", formatter: "{b}: {c} 人 ({d}%)" },
    legend: { bottom: 0, textStyle: { fontSize: 12, color: "#606266" } },
    series: [{
      type: "pie",
      radius: ["45%", "70%"],
      center: ["50%", "45%"],
      avoidLabelOverlap: true,
      itemStyle: { borderRadius: 6, borderColor: "#fff", borderWidth: 2 },
      label: { show: true, formatter: "{b}\n{c}人", fontSize: 12, color: "#303133" },
      labelLine: { show: true },
      data: items,
    }],
  });
  state.chartInstances.push(chart);
}

function renderProgressChart(data) {
  const el = document.querySelector("#chart-progress");
  if (!el) return;
  const chart = echarts.init(el);
  const keys = Object.keys(data);
  const values = keys.map((k) => data[k]);
  chart.setOption({
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    grid: { left: "5%", right: "5%", bottom: "8%", top: "12%", containLabel: true },
    xAxis: {
      type: "category",
      data: keys,
      axisLine: { lineStyle: { color: "#DCDFE6" } },
      axisLabel: { color: "#606266", fontSize: 12 },
    },
    yAxis: {
      type: "value",
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: "#EBEEF5" } },
      axisLabel: { color: "#909399", fontSize: 12 },
    },
    series: [{
      data: values,
      type: "bar",
      barWidth: "50%",
      itemStyle: {
        color: {
          type: "linear", x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: "#5a9ee0" },
            { offset: 1, color: "#357ABD" },
          ],
        },
        borderRadius: [6, 6, 0, 0],
      },
      label: { show: true, position: "top", color: "#606266", fontSize: 12 },
    }],
  });
  state.chartInstances.push(chart);
}

function renderScoreChart(data) {
  const el = document.querySelector("#chart-score");
  if (!el) return;
  const chart = echarts.init(el);
  const keys = Object.keys(data);
  const values = keys.map((k) => data[k]);
  const total = values.reduce((a, b) => a + b, 0);
  const colorList = ["#F56C6C", "#E6A23C", "#909399", "#4A90D9", "#67C23A"];
  chart.setOption({
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, formatter: (p) => `${p[0].name}<br/>人数: ${p[0].value} (${total ? ((p[0].value/total)*100).toFixed(1) : 0}%)` },
    grid: { left: "3%", right: "4%", bottom: "6%", top: "10%", containLabel: true },
    xAxis: {
      type: "category",
      data: keys,
      axisLine: { lineStyle: { color: "#DCDFE6" } },
      axisLabel: { color: "#606266", fontSize: 12 },
    },
    yAxis: {
      type: "value",
      name: "人数",
      nameTextStyle: { color: "#909399", fontSize: 12 },
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: "#EBEEF5" } },
      axisLabel: { color: "#909399", fontSize: 12 },
    },
    series: [{
      data: values.map((v, i) => ({ value: v, itemStyle: { color: colorList[i], borderRadius: [6, 6, 0, 0] } })),
      type: "bar",
      barWidth: "45%",
      label: { show: true, position: "top", color: "#606266", fontSize: 12 },
    }],
  });
  state.chartInstances.push(chart);
}

/* ============================================
   学生管理
   ============================================ */
async function renderStudents(main) {
  const rows = await api("/api/students");
  main.innerHTML = html`
    <div class="page-header">
      <h2>学生管理</h2>
      <div class="actions">
        <button class="btn-success" onclick="openImportStudentsModal()">&#x1F4E5; 导入学生</button>
        <button class="btn-outline" onclick="downloadStudentTemplate()">&#x1F4C4; 下载模板</button>
      </div>
    </div>
    <div class="panel">
      <h3>账号规则说明</h3>
      <ul class="rule-list">
        <li>导入学生后系统自动创建账号，账号为<strong>学号</strong>，初始密码为<strong>学号后5位</strong></li>
        <li>支持从 Excel 文件批量导入，列依次为：<strong>学号 / 姓名 / 班级</strong></li>
        <li>重复导入同一学号将自动更新姓名、班级、密码</li>
      </ul>
    </div>
    <div class="table-wrap">
      <div class="table-header">
        <h3>学生列表</h3>
        <span class="hint">共 ${rows.length} 名学生</span>
      </div>
      <table>
        <thead>
          <tr><th>学号</th><th>姓名</th><th>班级</th><th>账号</th><th>初始密码</th></tr>
        </thead>
        <tbody>
          ${rows.length === 0 ? '<tr><td colspan="5" class="table-empty">暂无学生，请先导入</td></tr>' :
            rows.map((r) => `
              <tr>
                <td style="color:var(--primary);font-weight:600">${r.student_id}</td>
                <td>${r.name}</td>
                <td>${r.class_name}</td>
                <td><code>${r.student_id}</code></td>
                <td><code>${r.student_id.slice(-5)}</code></td>
              </tr>
            `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function openImportStudentsModal() {
  const overlay = document.createElement("div");
  overlay.className = "modal-overlay";
  overlay.innerHTML = html`
    <div class="modal-form">
      <div class="modal-form-header">
        <h3>&#x1F4E5; 导入学生名单</h3>
        <button type="button" class="close-btn" data-act="close">&#x2715;</button>
      </div>
      <label class="upload-zone" id="upload-zone">
        <div class="upload-icon">&#x1F4C2;</div>
        <div class="upload-text">点击此处选择 Excel 文件，或拖拽到此处</div>
        <div class="upload-hint">支持 .xlsx / .xls 格式，第一行为表头</div>
        <div class="file-name" id="file-name" style="display:none"></div>
        <input type="file" id="file-input" accept=".xlsx,.xls" />
      </label>
      <div class="field-hint">
        <strong>Excel 列顺序：</strong>学号 | 姓名 | 班级<br/>
        <strong>示例：</strong><br/>
        2024100101 | 张三 | 24移动互联3-1<br/>
        2024100102 | 李四 | 24移动互联3-1
      </div>
      <div class="import-template-link" style="margin-top:12px" onclick="downloadStudentTemplate()">
        &#x1F4C4; 没有模板？点击下载导入模板
      </div>
      <div class="form-actions">
        <button type="button" class="btn-outline" data-act="close">取消</button>
        <button type="button" class="btn-primary" id="confirm-import" disabled onclick="confirmImportStudents()">开始导入</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
  const close = () => overlay.remove();
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) close();
    if (e.target.dataset.act === "close" || e.target.closest('[data-act="close"]')) close();
  });
  // 文件选择
  const fileInput = overlay.querySelector("#file-input");
  const fileName = overlay.querySelector("#file-name");
  const confirmBtn = overlay.querySelector("#confirm-import");
  const uploadZone = overlay.querySelector("#upload-zone");
  fileInput.addEventListener("change", () => {
    if (fileInput.files.length) {
      const f = fileInput.files[0];
      fileName.textContent = `已选择: ${f.name} (${(f.size/1024).toFixed(1)} KB)`;
      fileName.style.display = "block";
      confirmBtn.disabled = false;
    }
  });
  // 拖拽支持
  uploadZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    uploadZone.classList.add("drag-over");
  });
  uploadZone.addEventListener("dragleave", () => uploadZone.classList.remove("drag-over"));
  uploadZone.addEventListener("drop", (e) => {
    e.preventDefault();
    uploadZone.classList.remove("drag-over");
    if (e.dataTransfer.files.length) {
      fileInput.files = e.dataTransfer.files;
      fileInput.dispatchEvent(new Event("change"));
    }
  });
}

async function confirmImportStudents() {
  const fileInput = document.querySelector("#file-input");
  if (!fileInput || !fileInput.files.length) {
    showToast("请先选择文件", "error");
    return;
  }
  const formData = new FormData();
  formData.append("file", fileInput.files[0]);
  const overlay = document.querySelector(".modal-overlay");
  const confirmBtn = document.querySelector("#confirm-import");
  confirmBtn.disabled = true;
  confirmBtn.textContent = "导入中...";
  try {
    const headers = {};
    if (state.token) headers.Authorization = `Bearer ${state.token}`;
    const res = await fetch("/api/students/import", { method: "POST", body: formData, headers });
    const body = await res.json();
    if (!res.ok || body.code !== 200) throw new Error(body.message || "导入失败");
    overlay.remove();
    showToast(`成功导入 ${body.data.count} 名学生`, "success");
    renderTeacher();
  } catch (err) {
    showToast(err.message, "error");
    confirmBtn.disabled = false;
    confirmBtn.textContent = "开始导入";
  }
}

function downloadStudentTemplate() {
  const csv = "\uFEFF学号,姓名,班级\n2024100101,张三,24移动互联3-1\n2024100102,李四,24移动互联3-1\n2024100103,王五,24移动互联3-2\n";
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "学生名单模板.csv";
  a.click();
  URL.revokeObjectURL(url);
  showToast("模板下载完成", "success");
}

/* ============================================
   试卷管理
   ============================================ */
async function renderPapers(main) {
  const rows = await api("/api/papers");
  main.innerHTML = html`
    <div class="page-header">
      <h2>试卷管理</h2>
    </div>
    <div class="table-wrap">
      <div class="table-header">
        <h3>试卷列表</h3>
      </div>
      <table>
        <thead>
          <tr><th>试卷编号</th><th>标题</th><th>总分</th><th>时长</th></tr>
        </thead>
        <tbody>
          ${rows.map((r) => `<tr><td style="color:var(--primary)">${r.paper_id}</td><td>${r.title}</td><td>${r.total_score}分</td><td>${r.duration_minutes}分钟</td></tr>`).join("")}
        </tbody>
      </table>
    </div>
  `;
}

/* ============================================
   考试管理
   ============================================ */
async function renderExamAdmin(main) {
  const [current, examList, papers] = await Promise.all([
    api("/api/exam/current"),
    api("/api/teacher/exams"),
    api("/api/papers"),
  ]);
  main.innerHTML = html`
    <div class="page-header">
      <h2>考试管理</h2>
      <div class="actions">
        <button class="btn-success" onclick="openCreateExamModal()">&#x2795; 创建考试</button>
      </div>
    </div>
    <div class="panel">
      <h2>当前考试</h2>
      <div class="exam-current-card">
        <div class="exam-current-info">
          <h3>${current.exam.name}</h3>
          <div class="exam-current-meta">
            <span>试卷: ${current.paper.title}</span>
            <span>时长: ${current.exam.duration_minutes} 分钟</span>
            <span>总分: ${current.paper.total_score} 分</span>
          </div>
          <div style="margin-top:10px">
            状态: <span class="status ${current.exam.status}">${statusName(current.exam.status)}</span>
            ${current.exam.start_time ? `<span class="hint" style="margin-left:12px">开始: ${formatTime(current.exam.start_time)}</span>` : ''}
          </div>
        </div>
        <div class="exam-current-actions">
          ${current.exam.status === 'waiting' || current.exam.status === 'ended' ?
            `<button class="btn-primary" onclick="startExamAction()">&#x25B6; 开始考试</button>` :
            `<button class="btn-primary" disabled style="opacity:0.5">&#x25B6; 考试进行中</button>`}
          ${current.exam.status === 'running' ?
            `<button class="btn-danger" onclick="endExamAction()">&#x25A0; 结束考试</button>` : ''}
          <button class="btn-outline" onclick="autoEndCheck()">&#x23F1; 检查时长</button>
        </div>
      </div>
    </div>
    <div class="table-wrap">
      <div class="table-header">
        <h3>考试列表（历史记录）</h3>
        <span class="hint">共 ${examList.length} 场考试</span>
      </div>
      <table>
        <thead>
          <tr><th>编号</th><th>考试名称</th><th>试卷</th><th>时长</th><th>状态</th><th>创建时间</th><th>操作</th></tr>
        </thead>
        <tbody>
          ${examList.length === 0 ? '<tr><td colspan="7" class="table-empty">暂无考试</td></tr>' :
            examList.map((e) => `
              <tr>
                <td style="color:var(--primary)">#${e.id}</td>
                <td>${e.name}</td>
                <td>${e.paper_title || e.paper_id}</td>
                <td>${e.duration_minutes} 分钟</td>
                <td><span class="status ${e.status}">${statusName(e.status)}</span></td>
                <td class="hint">${formatTime(e.created_at)}</td>
                <td>
                  <button class="btn-text btn-sm" onclick="setTeacherTab('monitor')">监控</button>
                  <button class="btn-text btn-sm" onclick="setTeacherTab('results')">成绩</button>
                </td>
              </tr>
            `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function formatTime(iso) {
  if (!iso) return '-';
  try {
    const d = new Date(iso);
    const pad = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  } catch (e) { return iso; }
}

async function startExamAction() {
  showModal({
    icon: "&#x25B6;&#xFE0F;",
    title: "开始考试",
    message: "考试开始后，学生可以登录并参加考试。是否立即开始？",
    confirmText: "开始考试",
    onConfirm: async () => {
      try {
        await api("/api/teacher/start-exam", { method: "POST", body: JSON.stringify({}) });
        showToast("考试已开始", "success");
        renderTeacher();
      } catch (err) {
        showToast(err.message, "error");
      }
    },
  });
}

async function endExamAction() {
  showModal({
    icon: "&#x26D4;",
    title: "结束考试",
    message: "结束考试后将自动收卷并判分。是否确认结束？",
    confirmText: "结束考试",
    danger: true,
    onConfirm: async () => {
      try {
        await api("/api/teacher/end-exam", { method: "POST", body: JSON.stringify({}) });
        showToast("考试已结束", "success");
        renderTeacher();
      } catch (err) {
        showToast(err.message, "error");
      }
    },
  });
}

async function autoEndCheck() {
  try {
    const data = await api("/api/teacher/auto-end-check", { method: "POST", body: JSON.stringify({}) });
    if (data.ended) {
      showModal({
        icon: "&#x23F0;",
        title: "考试已自动结束",
        message: `考试时长已到，已自动结束，并对 ${data.forced_count} 名未交卷学生执行了强制收卷。`,
        confirmText: "我知道了",
        cancelText: "关闭",
        onConfirm: () => renderTeacher(),
      });
    } else if (data.remaining_seconds) {
      const minutes = Math.floor(data.remaining_seconds / 60);
      showToast(`考试进行中，剩余 ${minutes} 分钟`, "info");
    } else {
      showToast("当前不在考试进行中", "info");
    }
  } catch (err) {
    showToast(err.message, "error");
  }
}

function openCreateExamModal() {
  api("/api/papers").then((papers) => {
    const overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    overlay.innerHTML = html`
      <form class="modal-form" onsubmit="submitCreateExam(event)">
        <div class="modal-form-header">
          <h3>&#x2795; 创建考试</h3>
          <button type="button" class="close-btn" data-act="close">&#x2715;</button>
        </div>
        <div class="field">
          <label>考试名称<span class="required">*</span></label>
          <input name="name" placeholder="例如: 2024-2025学年第一学期期末考试" required />
        </div>
        <div class="exam-form-grid">
          <div class="field">
            <label>选择试卷<span class="required">*</span></label>
            <select name="paper_id" class="filter-select" required>
              <option value="">请选择试卷</option>
              ${papers.map((p) => `<option value="${p.paper_id}">${p.paper_id} - ${p.title} (${p.total_score}分)</option>`).join("")}
            </select>
          </div>
          <div class="field">
            <label>考试时长（分钟）</label>
            <input name="duration_minutes" type="number" min="1" max="600" value="90" required />
            <div class="field-hint">默认 90 分钟</div>
          </div>
        </div>
        <div class="form-actions">
          <button type="button" class="btn-outline" data-act="close">取消</button>
          <button type="submit" class="btn-primary">创建考试</button>
        </div>
      </form>
    `;
    document.body.appendChild(overlay);
    const close = () => overlay.remove();
    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) close();
      if (e.target.dataset.act === "close") close();
    });
  });
}

async function submitCreateExam(event) {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(event.target));
  const overlay = event.target.closest(".modal-overlay");
  try {
    await api("/api/teacher/exams", { method: "POST", body: JSON.stringify(data) });
    overlay.remove();
    showToast("考试已创建，已设为当前考试", "success");
    renderTeacher();
  } catch (err) {
    showToast(err.message, "error");
  }
}

/* ============================================
   实时监控
   ============================================ */
async function renderMonitor(main) {
  const data = await api("/api/teacher/monitor");
  main.innerHTML = html`
    <div class="page-header">
      <h2>实时监控</h2>
    </div>
    <div class="stats-grid">
      <div class="stat-card-mini">
        <div class="stat-label">总人数</div>
        <div class="stat-value">${data.stats.total}</div>
      </div>
      <div class="stat-card-mini">
        <div class="stat-label" style="color:var(--primary)">已登录</div>
        <div class="stat-value" style="color:var(--primary)">${data.stats.logged_in}</div>
      </div>
      <div class="stat-card-mini">
        <div class="stat-label" style="color:var(--success)">答题中</div>
        <div class="stat-value" style="color:var(--success)">${data.stats.answering}</div>
      </div>
      <div class="stat-card-mini">
        <div class="stat-label" style="color:var(--warning)">已提交</div>
        <div class="stat-value" style="color:var(--warning)">${data.stats.submitted}</div>
      </div>
      <div class="stat-card-mini">
        <div class="stat-label">未登录</div>
        <div class="stat-value">${data.stats.not_logged_in}</div>
      </div>
    </div>
    <div class="table-wrap">
      <div class="table-header">
        <h3>学生状态列表</h3>
      </div>
      <table>
        <thead>
          <tr><th>学号</th><th>姓名</th><th>状态</th><th>答题进度</th><th>操作</th></tr>
        </thead>
        <tbody>
          ${data.students.map((s) => `
            <tr>
              <td>${s.student_id}</td>
              <td>${s.name}</td>
              <td><span class="status ${s.status}">${statusName(s.status)}</span></td>
              <td>${s.answered} / ${s.total}</td>
              <td>${s.submitted ? '<span class="hint">已提交</span>' : `<button class="btn-danger btn-sm" onclick="forceSubmit('${s.student_id}')">强制收卷</button>`}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
  state.monitorTimer = setTimeout(() => renderMonitor(main), 5000);
}

async function forceSubmit(studentId) {
  await api("/api/teacher/force-submit", { method: "POST", body: JSON.stringify({ student_id: studentId }) });
  renderTeacher();
}

/* ============================================
   批改评分
   ============================================ */
async function renderGrading(main) {
  const rows = await api("/api/grading/records");
  main.innerHTML = html`
    <div class="page-header">
      <h2>批改评分</h2>
      <span class="hint">进度: ${rows.length} 条待批改</span>
    </div>
    ${rows.length ? rows.map((r) => `
      <div class="panel">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
          <strong style="color:var(--text-primary)">${r.student_id} ${r.name} - 第 ${r.question_no} 题</strong>
          <span class="question-type-tag">简答题 ${r.max_score}分</span>
        </div>
        <p style="margin-bottom:8px;color:var(--text-regular)">${r.content}</p>
        <div class="answer-box">
          <div class="answer-label">学生答案:</div>
          <p>${r.answer_text || "未作答"}</p>
        </div>
        <div class="grading-score-input">
          <input style="max-width:140px" id="score${r.answer_id}" type="number" min="0" max="${r.max_score}" value="${r.score || 0}" placeholder="分数" />
          <button class="btn-primary" onclick="saveScore(${r.answer_id})">保存评分</button>
        </div>
      </div>
    `).join("") : '<div class="panel"><p style="text-align:center;color:var(--text-secondary)">暂无可批改的简答题答案</p></div>'}
  `;
}

async function saveScore(answerId) {
  const score = document.querySelector(`#score${answerId}`).value;
  await api("/api/grading/manual", { method: "POST", body: JSON.stringify({ answer_id: answerId, score }) });
  alert("评分已保存");
}

/* ============================================
   成绩管理
   ============================================ */
async function renderResults(main) {
  const data = await api("/api/teacher/results");
  main.innerHTML = html`
    <div class="page-header">
      <h2>成绩管理</h2>
      <button class="btn-success" onclick="downloadResults()">导出 Excel</button>
    </div>
    <div class="stats-grid">
      <div class="stat-card-flat">
        <div class="stat-label">平均分</div>
        <div class="stat-value">${data.stats.average}</div>
      </div>
      <div class="stat-card-flat">
        <div class="stat-label">最高分</div>
        <div class="stat-value" style="color:var(--success)">${data.stats.highest}</div>
      </div>
      <div class="stat-card-flat">
        <div class="stat-label">最低分</div>
        <div class="stat-value" style="color:var(--danger)">${data.stats.lowest}</div>
      </div>
      <div class="stat-card-flat">
        <div class="stat-label">及格率</div>
        <div class="stat-value" style="color:var(--primary)">${data.stats.pass_rate}%</div>
      </div>
    </div>
    <div class="table-wrap">
      <div class="table-header">
        <h3>成绩列表</h3>
      </div>
      <table>
        <thead>
          <tr><th>学号</th><th>姓名</th><th>客观分</th><th>主观分</th><th>总分</th><th>状态</th></tr>
        </thead>
        <tbody>
          ${data.results.map((r) => `
            <tr>
              <td>${r.student_id}</td>
              <td>${r.name}</td>
              <td>${r.objective_score}</td>
              <td>${r.subjective_score}</td>
              <td style="font-weight:700;color:var(--success)">${r.total_score}</td>
              <td><span class="status ${r.status || 'not_logged_in'}">${statusName(r.status || 'not_logged_in')}</span></td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
}

async function downloadResults() {
  const res = await api("/api/teacher/export");
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "exam-results.csv";
  a.click();
  URL.revokeObjectURL(url);
}

function statusName(status) {
  return {
    not_logged_in: "未登录",
    logged_in: "已登录",
    confirmed: "已确认",
    answering: "答题中",
    submitted: "已提交",
    forced: "已强制收卷",
    waiting: "待开始",
    running: "进行中",
    ended: "已结束",
  }[status] || status;
}

renderHome();
