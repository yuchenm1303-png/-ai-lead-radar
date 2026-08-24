"use client";

import { useMemo, useState } from "react";

type LeadStatus = "new" | "saved" | "contacted" | "ignored";
type Lead = {
  id: number;
  source: string;
  title: string;
  excerpt: string;
  category: string;
  score: number;
  age: string;
  budget: string;
  status: LeadStatus;
  signals: string[];
};

const seedLeads: Lead[] = [
  {
    id: 1,
    source: "小红书",
    title: "想找人做一个预约类微信小程序，有偿",
    excerpt: "工作室需要一个预约、时间段选择和后台查看订单的小程序，预算可以沟通。",
    category: "微信小程序",
    score: 96,
    age: "2 分钟前",
    budget: "预算待聊",
    status: "new",
    signals: ["有偿", "明确需求", "近期项目"],
  },
  {
    id: 2,
    source: "小红书",
    title: "公司准备做一个英文官网，求靠谱开发",
    excerpt: "主要用于海外客户展示产品，希望手机端适配，后续可能还要接询盘表单。",
    category: "企业官网",
    score: 93,
    age: "7 分钟前",
    budget: "未公开",
    status: "new",
    signals: ["公司项目", "找开发", "官网"],
  },
  {
    id: 3,
    source: "小红书",
    title: "有会 Python 数据处理的吗？急",
    excerpt: "手里有一批 Excel 数据需要清洗、合并和自动生成结果表，最好今天能沟通。",
    category: "Python / 数据",
    score: 87,
    age: "14 分钟前",
    budget: "可沟通",
    status: "saved",
    signals: ["急", "明确交付", "可远程"],
  },
  {
    id: 4,
    source: "小红书",
    title: "想学习前端，有没有推荐的课程",
    excerpt: "零基础准备学 HTML、CSS 和 JavaScript，大家有什么课程推荐吗？",
    category: "非项目",
    score: 9,
    age: "21 分钟前",
    budget: "—",
    status: "ignored",
    signals: ["学习咨询"],
  },
  {
    id: 5,
    source: "小红书",
    title: "想做一个类似这个页面的独立站",
    excerpt: "有参考网站，希望先做展示和询盘，后面再考虑支付。想了解大概价格和周期。",
    category: "独立站",
    score: 91,
    age: "28 分钟前",
    budget: "询价中",
    status: "contacted",
    signals: ["询价", "有参考", "交付意向"],
  },
];

const statusCopy: Record<LeadStatus, string> = {
  new: "新发现",
  saved: "已收藏",
  contacted: "已联系",
  ignored: "已忽略",
};

export default function Home() {
  const [leads, setLeads] = useState(seedLeads);
  const [filter, setFilter] = useState("high");
  const [query, setQuery] = useState("");
  const [monitoring, setMonitoring] = useState(true);

  const visibleLeads = useMemo(() => {
    return leads.filter((lead) => {
      const q = query.trim().toLowerCase();
      const queryMatch = !q || `${lead.title} ${lead.excerpt} ${lead.category}`.toLowerCase().includes(q);
      const filterMatch =
        filter === "all" ||
        (filter === "high" && lead.score >= 80) ||
        (filter === "new" && lead.status === "new") ||
        (filter === "contacted" && lead.status === "contacted");
      return queryMatch && filterMatch;
    });
  }, [filter, leads, query]);

  const updateStatus = (id: number, status: LeadStatus) => {
    setLeads((current) => current.map((lead) => (lead.id === id ? { ...lead, status } : lead)));
  };

  const highIntent = leads.filter((lead) => lead.score >= 80).length;
  const fresh = leads.filter((lead) => lead.status === "new").length;
  const contacted = leads.filter((lead) => lead.status === "contacted").length;

  return (
    <main className="app-shell">
      <div className="ambient ambient-a" />
      <div className="ambient ambient-b" />

      <header className="topbar glass">
        <a className="brand" href="#dashboard" aria-label="AI Lead Radar 首页">
          <span className="brand-mark">AR</span>
          <span className="brand-copy">
            <strong>AI Lead Radar</strong>
            <small>Opportunity Monitor</small>
          </span>
        </a>

        <nav className="portal-nav" aria-label="页面导航">
          <a href="#dashboard">雷达</a>
          <a href="#leads">潜客</a>
          <a href="#monitor">监控</a>
        </nav>

        <div className="service-status">
          <i className={monitoring ? "online" : "paused"} />
          <span>{monitoring ? "Monitoring" : "Paused"}</span>
        </div>
      </header>

      <section className="hero" id="dashboard">
        <div>
          <p className="eyebrow">AI · LEAD DISCOVERY</p>
          <h1>接单雷达</h1>
          <p className="hero-copy">持续筛选最新公开需求，把真正值得联系的开发项目优先送到你面前。</p>
        </div>
        <div className="hero-actions">
          <div className="updated-box glass-lite">
            <span>LAST SCAN</span>
            <strong>刚刚</strong>
          </div>
          <button className="primary-button" type="button" onClick={() => setMonitoring((value) => !value)}>
            {monitoring ? "暂停监控" : "继续监控"}
          </button>
        </div>
      </section>

      <section className="status-strip glass">
        <div><span>监控状态</span><strong>{monitoring ? "运行中" : "已暂停"}</strong></div>
        <div><span>当前平台</span><strong>小红书 · MVP</strong></div>
        <div><span>筛选策略</span><strong>AI 意向评分 ≥ 80</strong></div>
      </section>

      <section className="summary-grid">
        <article className="summary-card glass"><span>HIGH INTENT</span><h3>{highIntent}</h3><p>高意向机会</p></article>
        <article className="summary-card glass"><span>NEW</span><h3>{fresh}</h3><p>等待处理</p></article>
        <article className="summary-card glass"><span>CONTACTED</span><h3>{contacted}</h3><p>已联系项目</p></article>
        <article className="summary-card glass"><span>LATENCY</span><h3>&lt; 5m</h3><p>目标发现延迟</p></article>
      </section>

      <section className="workspace" id="leads">
        <article className="lead-panel glass">
          <div className="section-head">
            <div>
              <p className="kicker">LIVE OPPORTUNITIES</p>
              <h2>最新潜客</h2>
              <p>先用模拟数据把界面与工作流跑通，后续接真实数据源。</p>
            </div>
            <span className="result-count">{visibleLeads.length} 条</span>
          </div>

          <div className="toolbar">
            <div className="filter-row">
              {[
                ["high", "高意向"],
                ["new", "新发现"],
                ["contacted", "已联系"],
                ["all", "全部"],
              ].map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  className={filter === key ? "filter-chip active" : "filter-chip"}
                  onClick={() => setFilter(key)}
                >
                  {label}
                </button>
              ))}
            </div>
            <input
              className="search-input"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索需求 / 技术 / 关键词"
            />
          </div>

          <div className="lead-list">
            {visibleLeads.map((lead) => (
              <article className="lead-card" key={lead.id}>
                <div className="lead-score" data-level={lead.score >= 90 ? "hot" : lead.score >= 80 ? "warm" : "low"}>
                  <strong>{lead.score}</strong>
                  <span>AI SCORE</span>
                </div>
                <div className="lead-main">
                  <div className="lead-meta">
                    <span className="source-pill">{lead.source}</span>
                    <span>{lead.age}</span>
                    <span>{lead.category}</span>
                    <span className={`status-pill status-${lead.status}`}>{statusCopy[lead.status]}</span>
                  </div>
                  <h3>{lead.title}</h3>
                  <p>{lead.excerpt}</p>
                  <div className="signal-row">
                    {lead.signals.map((signal) => <span key={signal}>{signal}</span>)}
                    <span>{lead.budget}</span>
                  </div>
                </div>
                <div className="lead-actions">
                  <button type="button" className="action-button strong" onClick={() => updateStatus(lead.id, "contacted")}>标记已联系</button>
                  <button type="button" className="action-button" onClick={() => updateStatus(lead.id, "saved")}>收藏</button>
                  <button type="button" className="action-button ghost" onClick={() => updateStatus(lead.id, "ignored")}>忽略</button>
                </div>
              </article>
            ))}
            {visibleLeads.length === 0 && <div className="empty-state">没有符合当前筛选条件的项目。</div>}
          </div>
        </article>

        <aside className="side-stack">
          <article className="glass side-card">
            <div className="section-head compact">
              <div><p className="kicker">MATCH PROFILE</p><h2>我的接单范围</h2></div>
            </div>
            <div className="tag-cloud">
              <span>微信小程序</span><span>网页开发</span><span>企业官网</span><span>独立站</span>
              <span>Python</span><span>数据处理</span><span>AI 工具</span><span>自动化</span>
            </div>
            <div className="mini-rule"><span>最低提醒分</span><strong>80 / 100</strong></div>
            <div className="mini-rule"><span>优先时效</span><strong>30 分钟内</strong></div>
          </article>

          <article className="glass side-card">
            <div className="section-head compact"><div><p className="kicker">DELIVERY</p><h2>通知</h2></div></div>
            <div className="notification-row"><span><i className="dot active" />网页实时提醒</span><strong>开启</strong></div>
            <div className="notification-row"><span><i className="dot" />手机推送</span><strong>待接入</strong></div>
            <div className="notification-row"><span><i className="dot" />企业微信 / 飞书</span><strong>待接入</strong></div>
          </article>
        </aside>
      </section>

      <section className="monitor-section glass" id="monitor">
        <div className="section-head">
          <div>
            <p className="kicker">MONITOR CONFIGURATION</p>
            <h2>监控策略</h2>
            <p>第一版聚焦开发需求发现，不做自动私信和批量营销。</p>
          </div>
          <span className="safe-pill">ASSISTED MONITORING</span>
        </div>
        <div className="monitor-grid">
          <div className="monitor-item"><span>平台</span><strong>小红书</strong><small>优先接入公开搜索结果</small></div>
          <div className="monitor-item"><span>发现词</span><strong>有偿 · 找人 · 求开发</strong><small>与技术类别组合判断</small></div>
          <div className="monitor-item"><span>AI 判断</span><strong>需求 / 意向 / 匹配度</strong><small>过滤学习、教程和招聘噪音</small></div>
          <div className="monitor-item"><span>数据原则</span><strong>最小化保存</strong><small>保留摘要、时间、链接和评分</small></div>
        </div>
      </section>

      <footer className="footer">
        <span>© 2026 AI Lead Radar</span>
        <span>公开需求发现 · AI 辅助筛选 · 人工联系</span>
      </footer>
    </main>
  );
}
