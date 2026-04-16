# JellyfishBot Design System

**完整设计规范文档 v1.0**

---

## 📖 目录

1. [品牌概述](#1-品牌概述)
2. [色彩系统](#2-色彩系统)
3. [字体系统](#3-字体系统)
4. [间距系统](#4-间距系统)
5. [圆角系统](#5-圆角系统)
6. [阴影系统](#6-阴影系统)
7. [图标系统](#7-图标系统)
8. [组件库](#8-组件库)
   - 8.1 按钮组件
   - 8.2 表单组件
   - 8.3 卡片与容器
   - 8.4 状态指示器
   - 8.5 表格与列表
   - 8.6 导航组件
   - 8.7 消息组件
9. [布局规范](#9-布局规范)
10. [交互动效](#10-交互动效)
11. [响应式策略](#11-响应式策略)
12. [开发实施指南](#12-开发实施指南)

---

## 1. 品牌概述

### 1.1 品牌定位

JellyfishBot 是一个专业的 AI Agent 平台，提供对话式 AI 交互、服务管理、定时任务、微信集成等功能。

### 1.2 设计理念

- **友好可爱**：像素风水母 logo 传达亲和力和趣味性
- **专业可靠**：暗色主题体现技术感和专业性
- **高效清晰**：简洁的界面设计，突出核心功能

### 1.3 视觉风格

- **主题**：暗色模式（Dark Theme）
- **风格**：现代科技感 + 友好可爱
- **特色**：品牌色点缀，微妙的像素艺术元素

---

## 2. 色彩系统

### 2.1 品牌色（Brand Colors）

#### 主品牌色 (Primary)

| 属性 | 值 |
|------|------|
| 颜色名称 | Pink Purple |
| HEX | `#E89FD9` |
| RGB | 232, 159, 217 |
| 用途 | CTA 按钮、重要操作、主要交互元素 |

#### 次要品牌色 (Secondary)

| 属性 | 值 |
|------|------|
| 颜色名称 | Blue Purple |
| HEX | `#8B7FD9` |
| RGB | 139, 127, 217 |
| 用途 | 链接、次要按钮、辅助交互元素 |

#### 强调色 (Accent)

| 属性 | 值 |
|------|------|
| 颜色名称 | Cyan Blue |
| HEX | `#5FC9E6` |
| RGB | 95, 201, 230 |
| 用途 | 成功状态、进度指示、正面反馈 |

#### 高亮色 (Highlight)

| 属性 | 值 |
|------|------|
| 颜色名称 | Bright Pink |
| HEX | `#FF8FCC` |
| RGB | 255, 143, 204 |
| 用途 | 通知、警示、需要注意的信息 |

#### 过渡色 (Legacy)

| 属性 | 值 |
|------|------|
| 颜色名称 | Purple |
| HEX | `#6c5ce7` |
| RGB | 108, 92, 231 |
| 用途 | 保留原有紫色，作为过渡使用 |

### 2.2 背景色（Background Colors）

**三层背景系统**

| 层级 | 名称 | HEX | RGB | 用途 |
|------|------|------|------|------|
| Layer 1 | 基础背景 (Base) | `#0f0f13` | 15, 15, 19 | 页面主背景 |
| Layer 2 | 卡片背景 (Card) | `#16161d` | 22, 22, 29 | 卡片、面板、侧边栏背景 |
| Layer 3 | 浮起元素 (Elevated) | `#1c1c27` | 28, 28, 39 | 输入框、按钮、悬浮元素背景 |

### 2.3 文字颜色（Text Colors）

| 名称 | HEX | RGB | 用途 |
|------|------|------|------|
| 主要文字 (Primary) | `#e4e4ed` | 228, 228, 237 | 标题、正文、重要信息 |
| 次要文字 (Secondary) | `#9494a8` | 148, 148, 168 | 说明文字、辅助信息、占位符 |

### 2.4 功能色（Functional Colors）

| 功能 | HEX | RGB | 用途 |
|------|------|------|------|
| 成功 (Success) | `#5FC9E6` | 95, 201, 230 | 成功提示、完成状态 |
| 警告 (Warning) | `#FFB86C` | 255, 184, 108 | 警告提示、待处理状态 |
| 错误 (Error) | `#FF6B9D` | 255, 107, 157 | 错误提示、失败状态 |
| 信息 (Info) | `#8B7FD9` | 139, 127, 217 | 信息提示、进行中状态 |

### 2.5 色彩使用原则

**60-30-10 配色法则**

- **60%**：背景色（暗色系统）
- **30%**：辅助色（品牌色作为点缀）
- **10%**：强调色（高亮和功能色）

**对比度要求**

- 正文文字与背景对比度 ≥ 4.5:1
- 大标题与背景对比度 ≥ 3:1
- 交互元素与背景对比度 ≥ 3:1

**品牌色应用优先级**

1. CTA 按钮、主要操作 → Primary (`#E89FD9`)
2. 链接、次要操作 → Secondary (`#8B7FD9`)
3. 成功反馈、进度 → Accent (`#5FC9E6`)
4. 通知、警示 → Highlight (`#FF8FCC`)

---

## 3. 字体系统

### 3.1 字体家族

```css
/* 界面字体 */
font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont,
             'Helvetica Neue', Arial, sans-serif;

/* 代码字体 */
font-family: 'JetBrains Mono', 'Consolas', 'Monaco',
             'Courier New', monospace;
```

### 3.2 字体层级

#### 标题层级 (Headings)

| 层级 | 字号 | 字重 | 行高 | 颜色 | 用途 |
|------|------|------|------|------|------|
| H1 | 32px (2rem) | 700 Bold | 40px (1.25) | `#e4e4ed` | 页面主标题 |
| H2 | 24px (1.5rem) | 700 Bold | 32px (1.33) | `#e4e4ed` | 主要区域标题 |
| H3 | 20px (1.25rem) | 600 Semibold | 28px (1.4) | `#e4e4ed` | 子区域标题 |
| H4 | 16px (1rem) | 600 Semibold | 24px (1.5) | `#e4e4ed` | 卡片、组件标题 |

#### 正文层级 (Body Text)

| 层级 | 字号 | 字重 | 行高 | 颜色 | 用途 |
|------|------|------|------|------|------|
| Large Body | 16px (1rem) | 400 Regular | 24px (1.5) | `#e4e4ed` | 重要正文内容 |
| Medium Body | 14px (0.875rem) | 400 Regular | 22px (1.57) | `#e4e4ed` | 标准正文内容 |
| Small Body | 12px (0.75rem) | 400 Regular | 20px (1.67) | `#9494a8` | 次要信息、说明文字 |
| Caption | 11px (0.6875rem) | 400 Regular | 16px (1.45) | `#9494a8` | 辅助文字、时间戳 |

#### 代码文本 (Code Text)

**Inline Code**

| 属性 | 值 |
|------|------|
| 字号 | 14px (0.875rem) |
| 字体 | JetBrains Mono |
| 颜色 | `#5FC9E6` |
| 背景 | `rgba(95, 201, 230, 0.1)` |
| 圆角 | 4px |
| 内边距 | 2px 6px |

**Code Block**

| 属性 | 值 |
|------|------|
| 字号 | 13px (0.8125rem) |
| 字体 | JetBrains Mono |
| 颜色 | `#e4e4ed` |
| 背景 | `#16161d` |
| 圆角 | 8px |
| 内边距 | 16px |

### 3.3 字体使用原则

- **层级清晰**：通过字号、字重、颜色建立清晰的信息层级
- **可读性优先**：正文字号不小于 14px，行高保持 1.5 以上
- **一致性**：同类信息使用相同的字体样式
- **代码区分**：代码内容统一使用等宽字体

---

## 4. 间距系统

### 4.1 间距 Token

| Token | 值 | REM |
|-------|------|------|
| XXS | 4px | 0.25rem |
| XS | 8px | 0.5rem |
| S | 12px | 0.75rem |
| M | 16px | 1rem |
| L | 24px | 1.5rem |
| XL | 32px | 2rem |
| XXL | 48px | 3rem |

### 4.2 间距应用规则

**组件内间距 (Component Padding)**

| 组件类型 | 间距 |
|----------|------|
| 小组件（按钮、标签） | 8px 12px |
| 标准组件（输入框、卡片） | 12px 16px |
| 大组件（模态框、面板） | 24px 32px |

**组件间距 (Component Margin)**

| 类型 | 间距 |
|------|------|
| 紧密排列 | 8px |
| 标准间距 | 16px |
| 松散间距 | 24px |
| 区域分隔 | 32px - 48px |

**布局间距 (Layout Spacing)**

| 区域 | 间距 |
|------|------|
| 页面边距 | 24px |
| 内容区域边距 | 32px |
| 区域之间间距 | 48px |

### 4.3 间距使用原则

- **8px 基准**：所有间距都是 8px 的倍数（4px 用于微调）
- **呼吸感**：避免元素过于拥挤，保持适当留白
- **一致性**：相同层级的元素使用相同间距
- **响应式**：小屏幕适当减少间距

---

## 5. 圆角系统

### 5.1 圆角 Token

| Token | 值 | 用途 |
|-------|------|------|
| Small | 4px | 标签、小按钮、Badge |
| Standard | 8px | 卡片、输入框、标准按钮 |
| Large | 12px | 模态框、大卡片、面板 |
| Special | 16px | 消息气泡、特殊组件 |

### 5.2 圆角应用规则

| 组件 | 圆角 |
|------|------|
| 按钮 | 8px |
| 输入框 | 8px |
| 卡片 | 8px |
| 模态框 | 12px |
| 消息气泡（用户） | 16px 8px 8px 16px |
| 消息气泡（Agent） | 8px 16px 16px 8px |
| 标签/Badge | 4px |
| 图片缩略图 | 8px |

### 5.3 圆角使用原则

- **统一性**：同类组件使用相同圆角
- **层级感**：大容器用大圆角，小元素用小圆角
- **特殊性**：消息气泡使用不对称圆角增强方向感

---

## 6. 阴影系统

### 6.1 阴影 Token

| Token | 值 | 用途 |
|-------|------|------|
| Elevated | `0 2px 8px rgba(0, 0, 0, 0.3)` | 卡片、按钮、小组件 |
| Floating | `0 4px 16px rgba(0, 0, 0, 0.4)` | 模态框、下拉菜单、悬浮面板 |
| Emphasized | `0 8px 24px rgba(232, 159, 217, 0.2)` | 重要元素、品牌色发光效果 |
| Focus | `0 0 0 3px rgba(232, 159, 217, 0.3)` | 输入框聚焦、键盘导航 |

### 6.2 阴影应用规则

| 场景 | 阴影 |
|------|------|
| 静态卡片 | Elevated |
| 悬浮卡片 (hover) | Floating |
| 模态框/抽屉 | Floating |
| 下拉菜单 | Floating |
| 输入框聚焦 | Focus |
| 品牌元素强调 | Emphasized |

### 6.3 阴影使用原则

- **层级表达**：阴影深度表示元素的层级高度
- **交互反馈**：hover 状态增强阴影
- **品牌融合**：重要元素使用带品牌色的阴影
- **性能考虑**：避免过度使用复杂阴影

---

## 7. 图标系统

### 7.1 图标风格

| 属性 | 值 |
|------|------|
| 风格 | 线性 (Linear/Outline) |
| 描边粗细 | 2px |
| 圆角 | 圆润 |
| 图标库 | Phosphor Icons (`@phosphor-icons/react`) |

### 7.2 图标尺寸

| 尺寸 | 值 | 用途 |
|------|------|------|
| Small | 16px | 行内图标、小按钮 |
| Standard | 24px | 标准按钮、菜单项 |
| Large | 32px | 大按钮、特色图标 |
| XLarge | 48px | 空状态、品牌展示 |

### 7.3 常用图标清单

**导航类**

| 用途 | Phosphor 图标 |
|------|------|
| 对话 | `ChatCircle` |
| 服务管理 | `Stack` |
| 定时任务 | `Timer` |
| 微信接入 | `ChatTeardropDots` |
| 文件管理 | `FolderOpen` |
| 用户 | `UserCircle` |

**操作类**

| 用途 | Phosphor 图标 |
|------|------|
| 添加 | `Plus` |
| 编辑 | `PencilSimple` |
| 删除 | `Trash` |
| 确认/成功 | `Check` / `CheckCircle` |
| 关闭/取消 | `X` / `XCircle` |
| 搜索 | `MagnifyingGlass` |
| 刷新 | `ArrowsClockwise` |
| 下载 | `DownloadSimple` |
| 上传 | `UploadSimple` |
| 复制 | `Copy` |

**状态类**

| 用途 | Phosphor 图标 |
|------|------|
| 信息 | `Info` |
| 警告 | `Warning` |
| 错误 | `XCircle` |
| 成功 | `CheckCircle` |
| 加载中 | `CircleNotch` (spinning) |

**功能类**

| 用途 | Phosphor 图标 |
|------|------|
| 语音输入 | `Microphone` |
| 附件 | `Paperclip` |
| 图片 | `ImageSquare` |
| 文件 | `FileText` |
| 链接 | `LinkSimple` |
| 更多选项 | `DotsThreeVertical` |
| 发送 | `PaperPlaneRight` |
| 退出 | `SignOut` |
| 设置 | `GearSix` |

**Chat 流式块图标**

| 用途 | Phosphor 图标 |
|------|------|
| 思考块 | `Brain` |
| 工具调用 | `Wrench` |
| 子代理 | `Robot` |
| 审批 | `LockSimple` |
| 文件操作 | `FileCode` |
| 计划 | `ListChecks` |

### 7.4 图标使用原则

- **风格统一**：全站使用 Phosphor Icons
- **语义清晰**：图标含义明确，必要时配文字
- **尺寸适配**：根据使用场景选择合适尺寸
- **颜色继承**：图标颜色继承父元素文字颜色

---

## 8. 组件库

### 8.1 按钮组件 (Buttons)

#### 8.1.1 主要按钮 (Primary Button)

```css
/* 默认状态 */
background: #E89FD9;
color: #ffffff;
border: none;
border-radius: 8px;
padding: 8px 16px;  /* 小 */
padding: 10px 20px; /* 中 */
padding: 12px 24px; /* 大 */
font-size: 14px;
font-weight: 500;
cursor: pointer;
transition: all 0.2s ease;

/* Hover 状态 */
background: linear-gradient(135deg, #E89FD9 0%, #FF8FCC 100%);
box-shadow: 0 4px 12px rgba(232, 159, 217, 0.4);
transform: translateY(-1px);

/* Active 状态 */
transform: translateY(0);
box-shadow: 0 2px 6px rgba(232, 159, 217, 0.3);

/* Disabled 状态 */
opacity: 0.5;
cursor: not-allowed;
```

**使用场景**：主要操作、提交表单、确认操作

#### 8.1.2 次要按钮 (Secondary Button)

```css
/* 默认状态 */
background: #8B7FD9;
color: #ffffff;

/* Hover 状态 */
background: linear-gradient(135deg, #8B7FD9 0%, #6c5ce7 100%);
box-shadow: 0 4px 12px rgba(139, 127, 217, 0.4);
```

**使用场景**：次要操作、取消、返回

#### 8.1.3 幽灵按钮 (Ghost Button)

```css
/* 默认状态 */
background: transparent;
color: #E89FD9;
border: 1px solid #E89FD9;
border-radius: 8px;
padding: 8px 16px;

/* Hover 状态 */
background: rgba(232, 159, 217, 0.1);
border-color: #FF8FCC;
color: #FF8FCC;

/* Active 状态 */
background: rgba(232, 159, 217, 0.2);
```

**使用场景**：轻量操作、可选操作

#### 8.1.4 文字按钮 (Text Button)

```css
/* 默认状态 */
background: transparent;
color: #8B7FD9;
border: none;
padding: 4px 8px;

/* Hover 状态 */
color: #E89FD9;
text-decoration: underline;
```

**使用场景**：链接、内联操作

#### 8.1.5 图标按钮 (Icon Button)

```css
/* 默认状态 */
width: 32px;  /* 小 */
width: 40px;  /* 中 */
width: 48px;  /* 大 */
height: same as width;
border-radius: 50%;
background: transparent;
color: #e4e4ed;
display: flex;
align-items: center;
justify-content: center;

/* Hover 状态 */
background: rgba(232, 159, 217, 0.1);
color: #E89FD9;
```

**使用场景**：工具栏、快捷操作

### 8.2 表单组件 (Form Components)

#### 8.2.1 输入框 (Input)

```css
/* 默认状态 */
background: #1c1c27;
border: 1px solid transparent;
border-radius: 8px;
padding: 10px 12px;
font-size: 14px;
color: #e4e4ed;
transition: all 0.2s ease;

/* Placeholder */
::placeholder {
  color: #9494a8;
}

/* Focus 状态 */
border-color: #E89FD9;
box-shadow: 0 0 0 3px rgba(232, 159, 217, 0.2);
outline: none;

/* Error 状态 */
border-color: #FF6B9D;
box-shadow: 0 0 0 3px rgba(255, 107, 157, 0.2);

/* Disabled 状态 */
opacity: 0.5;
cursor: not-allowed;
```

**标签 (Label)**

```css
font-size: 14px;
font-weight: 500;
color: #e4e4ed;
margin-bottom: 8px;
display: block;
```

**辅助文字 (Helper Text)**

```css
font-size: 12px;
color: #9494a8;
margin-top: 4px;
```

**错误提示 (Error Message)**

```css
font-size: 12px;
color: #FF6B9D;
margin-top: 4px;
```

#### 8.2.2 文本域 (Textarea)

```css
/* 基础样式同 Input */
min-height: 80px;
resize: vertical;
line-height: 1.5;
```

#### 8.2.3 下拉选择 (Select)

```css
/* 选择框 */
background: #1c1c27;
border: 1px solid transparent;
border-radius: 8px;
padding: 10px 36px 10px 12px;
font-size: 14px;
color: #e4e4ed;
cursor: pointer;

/* 下拉菜单 */
.dropdown-menu {
  background: #16161d;
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
  margin-top: 4px;
  max-height: 240px;
  overflow-y: auto;
}

/* 选项 */
.option:hover {
  background: rgba(232, 159, 217, 0.1);
}

.option.selected {
  background: rgba(232, 159, 217, 0.2);
  color: #E89FD9;
}
```

#### 8.2.4 复选框 (Checkbox)

```css
.checkbox-input {
  width: 18px;
  height: 18px;
  border: 2px solid #9494a8;
  border-radius: 4px;
  background: transparent;
  transition: all 0.2s;
}

.checkbox-input.checked {
  background: #E89FD9;
  border-color: #E89FD9;
}
```

#### 8.2.5 单选框 (Radio)

```css
.radio-input {
  border-radius: 50%;
}

.radio-input.checked::after {
  width: 8px;
  height: 8px;
  background: #ffffff;
  border-radius: 50%;
}
```

#### 8.2.6 开关 (Switch)

```css
.switch-track {
  width: 44px;
  height: 24px;
  background: #9494a8;
  border-radius: 12px;
  transition: background 0.3s;
}

.switch-track.on {
  background: linear-gradient(135deg, #E89FD9 0%, #8B7FD9 100%);
}

.switch-thumb {
  width: 20px;
  height: 20px;
  background: #ffffff;
  border-radius: 50%;
  transition: transform 0.3s;
}

.switch-track.on .switch-thumb {
  transform: translateX(20px);
}
```

#### 8.2.7 标签/芯片 (Tags/Chips)

```css
.tag {
  display: inline-flex;
  align-items: center;
  padding: 4px 12px;
  background: rgba(232, 159, 217, 0.2);
  color: #E89FD9;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 500;
}

/* 颜色变体 */
.tag-success { background: rgba(95, 201, 230, 0.2); color: #5FC9E6; }
.tag-warning { background: rgba(255, 184, 108, 0.2); color: #FFB86C; }
.tag-error   { background: rgba(255, 107, 157, 0.2); color: #FF6B9D; }
```

### 8.3 卡片与容器 (Cards & Containers)

#### 8.3.1 基础卡片 (Basic Card)

```css
.card {
  background: #1c1c27;
  border-radius: 8px;
  padding: 16px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
}

.card-header {
  font-size: 16px;
  font-weight: 600;
  color: #e4e4ed;
  margin-bottom: 12px;
}

.card-footer {
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid rgba(148, 148, 168, 0.2);
}
```

#### 8.3.2 交互卡片 (Interactive Card)

```css
.card-interactive {
  cursor: pointer;
  transition: all 0.2s ease;
  border: 1px solid transparent;
}

.card-interactive:hover {
  border-color: #E89FD9;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
  transform: translateY(-2px);
}
```

#### 8.3.3 描边卡片 (Outlined Card)

```css
.card-outlined {
  background: transparent;
  border: 1px solid rgba(148, 148, 168, 0.3);
  border-radius: 8px;
  padding: 16px;
}
```

#### 8.3.4 模态框 (Modal)

```css
.modal-overlay {
  background: rgba(0, 0, 0, 0.6);
  z-index: 1000;
}

.modal {
  background: #1c1c27;
  border-radius: 12px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.5);
  max-width: 600px;
  width: 90%;
  max-height: 80vh;
}

.modal-header {
  padding: 24px;
  border-bottom: 1px solid rgba(148, 148, 168, 0.2);
}

.modal-title {
  font-size: 20px;
  font-weight: 600;
  color: #e4e4ed;
}

.modal-close:hover {
  background: rgba(255, 107, 157, 0.1);
  color: #FF6B9D;
}

.modal-body {
  padding: 24px;
  overflow-y: auto;
  flex: 1;
}

.modal-footer {
  padding: 16px 24px;
  border-top: 1px solid rgba(148, 148, 168, 0.2);
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}
```

#### 8.3.5 面板/侧边栏 (Panel/Sidebar)

```css
.panel {
  background: #16161d;
  height: 100%;
  overflow-y: auto;
  border-right: 1px solid rgba(148, 148, 168, 0.1);
}

.panel-section-title {
  font-size: 12px;
  font-weight: 600;
  color: #9494a8;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
```

#### 8.3.6 消息气泡 (Message Bubble)

```css
/* 用户消息气泡 */
.message-bubble-user {
  background: #E89FD9;
  color: #ffffff;
  border-radius: 16px 8px 8px 16px;
  padding: 12px 16px;
  max-width: 70%;
  margin-left: auto;
}

/* Agent 消息气泡 */
.message-bubble-agent {
  background: #1c1c27;
  color: #e4e4ed;
  border-radius: 8px 16px 16px 8px;
  padding: 12px 16px;
  max-width: 70%;
  margin-right: auto;
}

/* 头像 */
.message-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: #8B7FD9;
}

/* 时间戳 */
.message-timestamp {
  font-size: 11px;
  color: #9494a8;
}
```

### 8.4 状态指示器 (Status Indicators)

#### 8.4.1 徽章 (Badges)

```css
.badge {
  display: inline-flex;
  align-items: center;
  padding: 4px 8px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 500;
}

.badge-success { background: rgba(95, 201, 230, 0.2); color: #5FC9E6; }
.badge-warning { background: rgba(255, 184, 108, 0.2); color: #FFB86C; }
.badge-error   { background: rgba(255, 107, 157, 0.2); color: #FF6B9D; }
.badge-info    { background: rgba(139, 127, 217, 0.2); color: #8B7FD9; }
.badge-neutral { background: rgba(148, 148, 168, 0.2); color: #9494a8; }
```

#### 8.4.2 状态点 (Status Dots)

```css
.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}

.status-dot-online  { background: #5FC9E6; box-shadow: 0 0 8px rgba(95, 201, 230, 0.6); }
.status-dot-away    { background: #FFB86C; }
.status-dot-offline { background: #FF6B9D; }
.status-dot-busy    { background: #8B7FD9; }

@keyframes pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50%      { opacity: 0.7; transform: scale(1.1); }
}

.status-dot-pulse {
  animation: pulse 2s ease-in-out infinite;
}
```

#### 8.4.3 加载动画 (Loading Spinners)

```css
@keyframes spin {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}

.spinner-primary {
  width: 24px;
  height: 24px;
  border: 3px solid rgba(232, 159, 217, 0.2);
  border-top-color: #E89FD9;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

.spinner-small  { width: 16px; height: 16px; border-width: 2px; }
.spinner-large  { width: 32px; height: 32px; border-width: 4px; }
```

#### 8.4.4 进度条 (Progress Bars)

```css
.progress-bar {
  width: 100%;
  height: 8px;
  background: #1c1c27;
  border-radius: 4px;
  overflow: hidden;
}

.progress-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, #E89FD9 0%, #8B7FD9 100%);
  border-radius: 4px;
  transition: width 0.3s ease;
}
```

#### 8.4.5 通知提示 (Notifications/Toast)

```css
.toast {
  background: #1c1c27;
  border-radius: 8px;
  padding: 16px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
  border-left: 4px solid;
  min-width: 320px;
  max-width: 480px;
}

.toast-success { border-left-color: #5FC9E6; }
.toast-warning { border-left-color: #FFB86C; }
.toast-error   { border-left-color: #FF6B9D; }
.toast-info    { border-left-color: #8B7FD9; }
```

### 8.5 表格与列表 (Tables & Lists)

#### 8.5.1 数据表格 (Data Table)

```css
.table {
  width: 100%;
  border-collapse: collapse;
  background: #1c1c27;
  border-radius: 8px;
  overflow: hidden;
}

.table thead { background: #16161d; }

.table th {
  padding: 12px 16px;
  font-size: 12px;
  font-weight: 600;
  color: #9494a8;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  border-bottom: 1px solid rgba(148, 148, 168, 0.2);
}

.table td {
  padding: 12px 16px;
  font-size: 14px;
  color: #e4e4ed;
  border-bottom: 1px solid rgba(148, 148, 168, 0.1);
}

/* 斑马纹 */
.table tbody tr:nth-child(even) { background: rgba(22, 22, 29, 0.5); }

/* Hover */
.table tbody tr:hover { background: rgba(232, 159, 217, 0.05); }

/* 选中 */
.table tbody tr.selected { background: rgba(232, 159, 217, 0.1); }
```

#### 8.5.2 列表项 (List Items)

```css
.list-item {
  background: #1c1c27;
  border-radius: 8px;
  padding: 12px 16px;
  margin-bottom: 8px;
  transition: all 0.2s;
  cursor: pointer;
}

.list-item:hover {
  border: 1px solid #E89FD9;
  background: rgba(232, 159, 217, 0.05);
}

.list-item.active {
  border-left: 3px solid #E89FD9;
  background: rgba(232, 159, 217, 0.1);
}
```

#### 8.5.3 菜单/下拉 (Menu/Dropdown)

```css
.menu {
  background: #16161d;
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
  padding: 8px 0;
  min-width: 200px;
}

.menu-item {
  padding: 10px 16px;
  font-size: 14px;
  color: #e4e4ed;
  cursor: pointer;
  transition: background 0.2s;
}

.menu-item:hover { background: rgba(232, 159, 217, 0.1); }

.menu-divider {
  height: 1px;
  background: rgba(148, 148, 168, 0.2);
  margin: 8px 0;
}
```

#### 8.5.4 标签页 (Tabs)

```css
.tab {
  padding: 12px 4px;
  font-size: 14px;
  font-weight: 500;
  color: #9494a8;
  cursor: pointer;
  border-bottom: 2px solid transparent;
  transition: color 0.2s;
}

.tab:hover { color: #e4e4ed; }

.tab.active {
  color: #E89FD9;
  border-bottom-color: #E89FD9;
}
```

### 8.6 导航组件 (Navigation Components)

#### 8.6.1 侧边导航 (Sidebar Navigation)

```css
.sidebar {
  width: 240px;
  background: #16161d;
  height: 100vh;
  display: flex;
  flex-direction: column;
  border-right: 1px solid rgba(148, 148, 168, 0.1);
  transition: width 0.3s ease;
}

.sidebar.collapsed { width: 64px; }

/* Logo 区域 */
.sidebar-logo {
  padding: 24px 16px;
  display: flex;
  align-items: center;
  gap: 12px;
  border-bottom: 1px solid rgba(148, 148, 168, 0.1);
}

.sidebar-logo-icon { width: 32px; height: 32px; }

.sidebar-logo-text {
  font-size: 18px;
  font-weight: 600;
  color: #e4e4ed;
}

.sidebar.collapsed .sidebar-logo-text { display: none; }

/* 导航项 */
.nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  margin-bottom: 4px;
  border-radius: 8px;
  font-size: 14px;
  color: #9494a8;
  cursor: pointer;
  transition: all 0.2s;
}

.nav-item:hover {
  background: rgba(232, 159, 217, 0.05);
  color: #e4e4ed;
}

.nav-item.active {
  background: rgba(232, 159, 217, 0.1);
  color: #E89FD9;
}

.nav-item.active::before {
  content: '';
  position: absolute;
  left: 0;
  width: 3px;
  height: 20px;
  background: #E89FD9;
  border-radius: 0 2px 2px 0;
}

/* 底部用户区 */
.sidebar-user-avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: #8B7FD9;
}

.sidebar.collapsed .sidebar-user-info { display: none; }
```

#### 8.6.2 面包屑 (Breadcrumb)

```css
.breadcrumb-item { color: #9494a8; }
.breadcrumb-item:hover { color: #E89FD9; cursor: pointer; }
.breadcrumb-item.active { color: #e4e4ed; cursor: default; }
```

### 8.7 消息组件 (Message Components)

#### 8.7.1 流式输出块 (Streaming Blocks)

**思考块 (Thinking Block)**

```css
.thinking-block {
  background: #16161d;
  border-left: 2px solid #8B7FD9;
  border-radius: 8px;
  padding: 12px 16px;
}

.thinking-icon { color: #8B7FD9; }

.thinking-title {
  font-size: 13px;
  font-weight: 500;
  color: #8B7FD9;
}

/* 三点跳动动画 */
@keyframes dot-bounce {
  0%, 80%, 100% { transform: translateY(0); }
  40%           { transform: translateY(-8px); }
}

.thinking-dot {
  width: 4px;
  height: 4px;
  background: #8B7FD9;
  border-radius: 50%;
  animation: dot-bounce 1.4s infinite ease-in-out;
}

.thinking-dot:nth-child(1) { animation-delay: -0.32s; }
.thinking-dot:nth-child(2) { animation-delay: -0.16s; }
```

**工具指示器 (Tool Indicator)**

```css
.tool-indicator {
  background: #1c1c27;
  border-left: 2px solid #5FC9E6;
  border-radius: 8px;
  padding: 12px 16px;
}

.tool-name {
  font-size: 13px;
  font-weight: 500;
  color: #5FC9E6;
}

.tool-status.running   { color: #FFB86C; }
.tool-status.completed { color: #5FC9E6; }

.tool-section-content {
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  background: #16161d;
  border-radius: 4px;
  padding: 8px;
}
```

**子代理卡片 (Subagent Card)**

```css
.subagent-card {
  background: #16161d;
  border: 1px solid #E89FD9;
  border-radius: 8px;
  padding: 16px;
}

.subagent-icon { color: #E89FD9; }

.subagent-name {
  font-size: 14px;
  font-weight: 600;
  color: #E89FD9;
}

.subagent-output {
  background: #1c1c27;
  border-radius: 6px;
  padding: 12px;
  font-size: 13px;
  line-height: 1.5;
}
```

**审批卡片 (Approval Card — HITL)**

```css
.approval-card {
  background: #1c1c27;
  border: 2px solid #FFB86C;
  border-radius: 8px;
  padding: 16px;
}

.approval-title {
  font-size: 14px;
  font-weight: 600;
  color: #FFB86C;
}

.approval-diff {
  font-family: 'JetBrains Mono', monospace;
  font-size: 12px;
  background: #16161d;
  border-radius: 6px;
  padding: 12px;
}

.diff-line-added   { background: rgba(95, 201, 230, 0.1); color: #5FC9E6; }
.diff-line-removed { background: rgba(255, 107, 157, 0.1); color: #FF6B9D; }

.approval-btn-approve { background: #5FC9E6; }
.approval-btn-reject  { background: #FF6B9D; }
.approval-btn-edit    { border-color: #8B7FD9; color: #8B7FD9; }
```

#### 8.7.2 图片附件 (Image Attachment)

```css
.image-attachment {
  width: 80px;
  height: 80px;
  border-radius: 8px;
  overflow: hidden;
  transition: transform 0.2s;
}

.image-attachment:hover { transform: scale(1.05); }

.image-attachment-remove {
  background: rgba(0, 0, 0, 0.6);
  border-radius: 50%;
  opacity: 0;
  transition: opacity 0.2s;
}

.image-attachment:hover .image-attachment-remove { opacity: 1; }
.image-attachment-remove:hover { background: #FF6B9D; }
```

#### 8.7.3 语音输入 (Voice Input)

```css
.voice-input-btn {
  width: 40px;
  height: 40px;
  border-radius: 50%;
  border: 2px solid #8B7FD9;
  color: #8B7FD9;
  transition: all 0.2s;
}

.voice-input-btn:hover { background: rgba(139, 127, 217, 0.1); }

.voice-input-btn.recording {
  background: #FF6B9D;
  border-color: #FF6B9D;
  color: #ffffff;
  animation: pulse-ring 1.5s ease-out infinite;
}

@keyframes pulse-ring {
  0%   { box-shadow: 0 0 0 0 rgba(255, 107, 157, 0.7); }
  70%  { box-shadow: 0 0 0 12px rgba(255, 107, 157, 0); }
  100% { box-shadow: 0 0 0 0 rgba(255, 107, 157, 0); }
}
```

---

## 9. 布局规范

### 9.1 主布局结构

```
┌──────────────────────────────────────────────────┐
│  Sidebar (240px / 64px collapsed)                │
│  ┌────────────────────────────────────────────┐  │
│  │  Logo & Brand                              │  │
│  ├────────────────────────────────────────────┤  │
│  │  Navigation Menu                           │  │
│  │  - Chat                                    │  │
│  │  - Services                                │  │
│  │  - Scheduler                               │  │
│  │  - WeChat                                  │  │
│  ├────────────────────────────────────────────┤  │
│  │  Footer Tools                              │  │
│  │  - Theme Toggle                            │  │
│  │  - File Panel                              │  │
│  │  - User Profile                            │  │
│  └────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────┐
│  Main Content Area                               │
│  ┌────────────────────────────────────────────┐  │
│  │  Header (64px)                             │  │
│  │  - Breadcrumb / Page Title                 │  │
│  │  - Actions / Search                        │  │
│  ├────────────────────────────────────────────┤  │
│  │  Content Body                              │  │
│  │  - Dynamic content based on route          │  │
│  └────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────┘
```

### 9.2 页面布局规范

**聊天页面布局 (Chat Page)**

```
┌──────────────────────────────────────────────────┐
│  Chat Header (64px)                              │
│  - Conversation Title                            │
│  - Actions (Clear, Export, Settings)             │
├──────────────────────────────────────────────────┤
│  Message Area (flex-grow)                        │
│  - Scrollable message list                       │
│  - Message bubbles                               │
│  - Tool indicators                               │
│  - Subagent cards                                │
├──────────────────────────────────────────────────┤
│  Input Area (auto-height, max 200px)             │
│  - Textarea with auto-expand                     │
│  - Toolbar (Attach, Voice, Send)                 │
└──────────────────────────────────────────────────┘
```

**服务管理页面布局 (Services Page)**

```
┌──────────────────────────────────────────────────┐
│  Page Header (64px)                              │
│  - Title: "Services"                             │
│  - Actions: [+ New Service] [Search]             │
├──────────────────────────────────────────────────┤
│  Filters & Tabs (48px)                           │
│  - Tabs: All / Active / Inactive                 │
│  - Filters: Type, Status                         │
├──────────────────────────────────────────────────┤
│  Service Grid (3 columns on desktop)             │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐         │
│  │ Service  │ │ Service  │ │ Service  │         │
│  │  Card    │ │  Card    │ │  Card    │         │
│  └──────────┘ └──────────┘ └──────────┘         │
└──────────────────────────────────────────────────┘
```

**定时任务页面布局 (Scheduler Page)**

```
┌──────────────────────────────────────────────────┐
│  Page Header (64px)                              │
│  - Title: "Scheduled Tasks"                      │
│  - Actions: [+ New Task] [Calendar View]         │
├──────────────────────────────────────────────────┤
│  Task Table                                      │
│  ┌──────────────────────────────────────────┐    │
│  │ Name | Schedule | Status | Actions       │    │
│  ├──────────────────────────────────────────┤    │
│  │ Task 1 | Daily 9AM  | Active | [Edit]    │    │
│  │ Task 2 | Weekly Mon | Paused | [Edit]    │    │
│  │ Task 3 | Monthly 1st| Active | [Edit]    │    │
│  └──────────────────────────────────────────┘    │
└──────────────────────────────────────────────────┘
```

### 9.3 网格系统

**12 列网格系统 (Grid System)**

| 断点 | 容器宽度 |
|------|----------|
| Desktop (≥1200px) | 1140px max-width |
| Tablet (768px–1199px) | 100% with 24px padding |
| Mobile (<768px) | 100% with 16px padding |

**列间距 (Gutter)**：Desktop 24px / Mobile 16px

**常用布局**

| 布局 | 列分配 |
|------|--------|
| 全宽 | 12 列 |
| 2 列 | 6 + 6 |
| 3 列 | 4 + 4 + 4 |
| 侧边栏 + 内容 | 3 + 9 或 4 + 8 |
| 主内容 + 侧边 | 8 + 4 或 9 + 3 |

### 9.4 Z-index 层级

| 层级 | Z-index | 用途 |
|------|---------|------|
| Base Layer | 0 | 页面基础内容 |
| Elevated | 10 | 卡片、按钮 |
| Dropdown | 100 | 下拉菜单、工具提示 |
| Sticky | 200 | 固定头部、侧边栏 |
| Overlay | 500 | 遮罩层 |
| Modal | 1000 | 模态框、抽屉 |
| Popover | 1100 | 弹出层、通知 |
| Toast | 1200 | 消息提示 |

---

## 10. 交互动效

### 10.1 过渡时间 (Transition Duration)

| Token | 值 | 用途 |
|-------|------|------|
| Fast | 0.1s | 微小变化（颜色、透明度） |
| Standard | 0.2s | 标准交互（hover、focus） |
| Moderate | 0.3s | 中等动画（展开、收起） |
| Slow | 0.5s | 复杂动画（页面切换、大型组件） |

### 10.2 缓动函数 (Easing Functions)

```css
/* 标准缓动 */
transition-timing-function: ease;

/* 进入动画 */
transition-timing-function: ease-out;

/* 退出动画 */
transition-timing-function: ease-in;

/* 平滑缓动 */
transition-timing-function: ease-in-out;

/* 自定义贝塞尔曲线 */
transition-timing-function: cubic-bezier(0.4, 0, 0.2, 1);
```

### 10.3 常用动画

**淡入淡出 (Fade)**

```css
@keyframes fadeIn {
  from { opacity: 0; }
  to   { opacity: 1; }
}

@keyframes fadeOut {
  from { opacity: 1; }
  to   { opacity: 0; }
}

.fade-in  { animation: fadeIn 0.3s ease-out; }
.fade-out { animation: fadeOut 0.2s ease-in; }
```

**滑入滑出 (Slide)**

```css
@keyframes slideInFromRight {
  from { transform: translateX(100%); opacity: 0; }
  to   { transform: translateX(0); opacity: 1; }
}

@keyframes slideInFromBottom {
  from { transform: translateY(20px); opacity: 0; }
  to   { transform: translateY(0); opacity: 1; }
}

.slide-in-right  { animation: slideInFromRight 0.3s ease-out; }
.slide-in-bottom { animation: slideInFromBottom 0.3s ease-out; }
```

**缩放 (Scale)**

```css
@keyframes scaleIn {
  from { transform: scale(0.9); opacity: 0; }
  to   { transform: scale(1); opacity: 1; }
}

.scale-in { animation: scaleIn 0.2s ease-out; }
```

**弹跳 (Bounce)**

```css
@keyframes bounce {
  0%, 20%, 50%, 80%, 100% { transform: translateY(0); }
  40%  { transform: translateY(-10px); }
  60%  { transform: translateY(-5px); }
}

.bounce { animation: bounce 1s ease; }
```

**摇晃 (Shake)**

```css
@keyframes shake {
  0%, 100%                { transform: translateX(0); }
  10%, 30%, 50%, 70%, 90% { transform: translateX(-4px); }
  20%, 40%, 60%, 80%      { transform: translateX(4px); }
}

.shake { animation: shake 0.5s ease; }
```

**打字效果 (Typing)**

```css
@keyframes typing {
  from { width: 0; }
  to   { width: 100%; }
}

@keyframes blink-caret {
  from, to { border-color: transparent; }
  50%      { border-color: #E89FD9; }
}

.typing-effect {
  overflow: hidden;
  border-right: 2px solid #E89FD9;
  white-space: nowrap;
  animation:
    typing 3.5s steps(40, end),
    blink-caret 0.75s step-end infinite;
}
```

### 10.4 交互状态动效

**按钮点击反馈**

```css
.button { transition: all 0.2s ease; }
.button:hover  { transform: translateY(-2px); box-shadow: 0 4px 12px rgba(232, 159, 217, 0.4); }
.button:active { transform: translateY(0); box-shadow: 0 2px 6px rgba(232, 159, 217, 0.3); }
```

**卡片悬浮效果**

```css
.card { transition: all 0.3s ease; }
.card:hover { transform: translateY(-4px); box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4); }
```

**输入框聚焦**

```css
.input { transition: all 0.2s ease; }
.input:focus {
  border-color: #E89FD9;
  box-shadow: 0 0 0 3px rgba(232, 159, 217, 0.2);
  transform: scale(1.01);
}
```

**加载骨架屏**

```css
@keyframes skeleton-loading {
  0%   { background-position: -200px 0; }
  100% { background-position: calc(200px + 100%) 0; }
}

.skeleton {
  background: linear-gradient(
    90deg,
    #1c1c27 0px,
    rgba(232, 159, 217, 0.1) 40px,
    #1c1c27 80px
  );
  background-size: 200px 100%;
  animation: skeleton-loading 1.5s ease-in-out infinite;
}
```

---

## 11. 响应式策略

### 11.1 断点定义 (Breakpoints)

```css
/* 移动设备 (Mobile) */
@media (max-width: 767px) { /* 小屏幕样式 */ }

/* 平板设备 (Tablet) */
@media (min-width: 768px) and (max-width: 1199px) { /* 中等屏幕样式 */ }

/* 桌面设备 (Desktop) */
@media (min-width: 1200px) { /* 大屏幕样式 */ }

/* 超大屏幕 (Large Desktop) */
@media (min-width: 1600px) { /* 超大屏幕样式 */ }
```

### 11.2 响应式布局调整

**侧边栏响应式**

| 断点 | 行为 |
|------|------|
| Desktop (≥1200px) | 240px 固定宽度，可折叠至 64px |
| Tablet (768px–1199px) | 64px 图标模式，或隐藏通过汉堡菜单调出 |
| Mobile (<768px) | 完全隐藏，通过汉堡菜单弹出全屏抽屉 |

**网格列数调整**

| 断点 | 列数 |
|------|------|
| Desktop (≥1200px) | 3–4 列 |
| Tablet (768px–1199px) | 2 列 |
| Mobile (<768px) | 1 列 |

**字体大小调整**

| 层级 | Desktop | Mobile |
|------|---------|--------|
| H1 | 32px | 24px |
| H2 | 24px | 20px |
| Body | 14px | 14px |

**间距调整**

| 区域 | Desktop | Tablet | Mobile |
|------|---------|--------|--------|
| 页面边距 | 32px | 24px | 16px |
| 卡片间距 | 24px | 16px | 12px |

### 11.3 移动端优化

**触摸目标尺寸**

- 最小触摸目标：44px × 44px
- 推荐触摸目标：48px × 48px
- 触摸目标间距：至少 8px

**移动端导航**

- 底部导航栏 (Tab Bar)
- 汉堡菜单 + 抽屉
- 返回按钮位置：左上角
- 主要操作：右上角或底部浮动按钮

**移动端表单**

- 输入框高度：至少 48px
- 标签位置：输入框上方
- 键盘类型：根据输入内容自动切换
- 自动聚焦：谨慎使用，避免意外弹出键盘

**移动端表格**

- 横向滚动
- 或转换为卡片列表
- 重要列固定，次要列可隐藏

### 11.4 响应式图片

```css
img { max-width: 100%; height: auto; }
```

```html
<!-- 使用 srcset 提供多分辨率 -->
<img
  src="image-1x.jpg"
  srcset="image-1x.jpg 1x, image-2x.jpg 2x, image-3x.jpg 3x"
  alt="Description"
/>

<!-- 使用 picture 元素 -->
<picture>
  <source media="(min-width: 1200px)" srcset="large.jpg">
  <source media="(min-width: 768px)" srcset="medium.jpg">
  <img src="small.jpg" alt="Description">
</picture>
```

---

## 12. 开发实施指南

### 12.1 技术栈

| 类别 | 技术选型 |
|------|----------|
| 前端框架 | React 19 |
| UI 组件库 | Ant Design 5（定制主题） |
| 路由 | React Router 7 |
| 样式方案 | CSS Modules + 全局 CSS 变量 |
| 构建工具 | Vite |
| 类型检查 | TypeScript 5.7 |
| 图标库 | Phosphor Icons (`@phosphor-icons/react`) |
| 代码字体 | JetBrains Mono (Google Fonts CDN) |

### 12.2 Ant Design 5 主题配置

> 参考实现：`frontend/src/styles/theme.ts`

```typescript
import type { ThemeConfig } from 'antd';

const theme: ThemeConfig = {
  token: {
    colorPrimary: '#E89FD9',
    colorSuccess: '#5FC9E6',
    colorWarning: '#FFB86C',
    colorError: '#FF6B9D',
    colorInfo: '#8B7FD9',

    colorBgBase: '#0f0f13',
    colorBgContainer: '#16161d',
    colorBgElevated: '#1c1c27',

    colorText: '#e4e4ed',
    colorTextSecondary: '#9494a8',

    colorBorder: 'rgba(148, 148, 168, 0.2)',
    colorBorderSecondary: 'rgba(148, 148, 168, 0.1)',

    fontFamily: "'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif",
    fontSize: 14,
    fontSizeHeading1: 32,
    fontSizeHeading2: 24,
    fontSizeHeading3: 20,
    fontSizeHeading4: 16,

    borderRadius: 8,
    borderRadiusLG: 12,
    borderRadiusSM: 4,

    boxShadow: '0 2px 8px rgba(0, 0, 0, 0.3)',
    boxShadowSecondary: '0 4px 16px rgba(0, 0, 0, 0.4)',

    motionDurationSlow: '0.3s',
    motionDurationMid: '0.2s',
    motionDurationFast: '0.1s',
  },

  components: {
    Button: {
      primaryColor: '#ffffff',
      colorPrimaryHover: '#FF8FCC',
      colorPrimaryActive: '#E89FD9',
    },
    Input: {
      colorBgContainer: '#1c1c27',
      activeBorderColor: '#E89FD9',
      hoverBorderColor: '#E89FD9',
      activeShadow: '0 0 0 3px rgba(232, 159, 217, 0.2)',
    },
    Card: {
      colorBgContainer: '#1c1c27',
    },
    Menu: {
      itemBg: 'transparent',
      itemSelectedBg: 'rgba(232, 159, 217, 0.1)',
      itemSelectedColor: '#E89FD9',
      itemHoverBg: 'rgba(232, 159, 217, 0.05)',
    },
    Table: {
      headerBg: '#16161d',
      rowHoverBg: 'rgba(232, 159, 217, 0.05)',
    },
    Modal: {
      contentBg: '#1c1c27',
      headerBg: '#1c1c27',
    },
  },
};

export default theme;
```

### 12.3 CSS 变量定义

> 参考实现：`frontend/src/styles/global.css`

```css
:root {
  /* 品牌色 */
  --color-primary: #E89FD9;
  --color-secondary: #8B7FD9;
  --color-accent: #5FC9E6;
  --color-highlight: #FF8FCC;
  --color-legacy: #6c5ce7;

  /* 背景色 */
  --bg-base: #0f0f13;
  --bg-card: #16161d;
  --bg-elevated: #1c1c27;

  /* 文字色 */
  --text-primary: #e4e4ed;
  --text-secondary: #9494a8;

  /* 功能色 */
  --color-success: #5FC9E6;
  --color-warning: #FFB86C;
  --color-error: #FF6B9D;
  --color-info: #8B7FD9;

  /* 间距 */
  --spacing-xxs: 4px;
  --spacing-xs: 8px;
  --spacing-s: 12px;
  --spacing-m: 16px;
  --spacing-l: 24px;
  --spacing-xl: 32px;
  --spacing-xxl: 48px;

  /* 圆角 */
  --radius-small: 4px;
  --radius-standard: 8px;
  --radius-large: 12px;
  --radius-special: 16px;

  /* 阴影 */
  --shadow-elevated: 0 2px 8px rgba(0, 0, 0, 0.3);
  --shadow-floating: 0 4px 16px rgba(0, 0, 0, 0.4);
  --shadow-emphasized: 0 8px 24px rgba(232, 159, 217, 0.2);
  --shadow-focus: 0 0 0 3px rgba(232, 159, 217, 0.3);

  /* 字体 */
  --font-ui: 'Segoe UI', -apple-system, BlinkMacSystemFont, sans-serif;
  --font-code: 'JetBrains Mono', Consolas, Monaco, 'Courier New', monospace;

  /* 过渡 */
  --transition-fast: 0.1s;
  --transition-standard: 0.2s;
  --transition-moderate: 0.3s;
  --transition-slow: 0.5s;
}
```

### 12.4 组件开发规范

**组件文件结构**

```
src/
├── components/
│   ├── Button/
│   │   ├── index.tsx
│   │   ├── Button.module.css
│   │   ├── Button.types.ts
│   │   └── Button.test.tsx
│   ├── Card/
│   └── ...
├── pages/
│   ├── Chat/
│   │   ├── index.tsx
│   │   ├── chat.module.css
│   │   ├── types.ts
│   │   ├── markdown.ts
│   │   ├── useSmartScroll.ts
│   │   └── components/
│   │       ├── MessageBubble.tsx
│   │       ├── ThinkingBlock.tsx
│   │       ├── ToolIndicator.tsx
│   │       ├── SubagentCard.tsx
│   │       ├── StreamingMessage.tsx
│   │       ├── ApprovalCard.tsx
│   │       ├── ImageAttachment.tsx
│   │       └── VoiceInput.tsx
│   ├── Login.tsx
│   ├── AdminServices/
│   ├── Scheduler/
│   └── WeChat/
├── layouts/
│   └── AppLayout.tsx
├── styles/
│   ├── theme.ts
│   └── global.css
├── services/
│   └── api.ts
└── types/
    └── index.ts
```

**组件命名规范**

```typescript
// 组件名: PascalCase
export const PrimaryButton: React.FC<ButtonProps> = () => {};

// Props 接口: 组件名 + Props
export interface ButtonProps {
  variant?: 'primary' | 'secondary' | 'ghost' | 'text';
  size?: 'small' | 'medium' | 'large';
  disabled?: boolean;
  loading?: boolean;
  icon?: React.ReactNode;
  onClick?: () => void;
  children: React.ReactNode;
}
```

### 12.5 可访问性 (Accessibility)

**键盘导航**

```tsx
<button
  onClick={handleClick}
  onKeyDown={(e) => {
    if (e.key === 'Enter' || e.key === ' ') handleClick();
  }}
  tabIndex={0}
  aria-label="Submit form"
>
  Submit
</button>
```

**ARIA 属性**

```tsx
<div
  role="dialog"
  aria-modal="true"
  aria-labelledby="modal-title"
  aria-describedby="modal-description"
>
  <h2 id="modal-title">Modal Title</h2>
  <p id="modal-description">Modal description</p>
</div>
```

**焦点管理**

```tsx
useEffect(() => {
  if (isOpen) {
    const firstFocusable = modalRef.current?.querySelector(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    );
    (firstFocusable as HTMLElement)?.focus();
  }
}, [isOpen]);
```

### 12.6 性能优化

**代码分割**

```tsx
import { lazy, Suspense } from 'react';

const ChatPage = lazy(() => import('./pages/Chat'));
const ServicesPage = lazy(() => import('./pages/AdminServices'));

function App() {
  return (
    <Suspense fallback={<LoadingSpinner />}>
      <Routes>
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/services" element={<ServicesPage />} />
      </Routes>
    </Suspense>
  );
}
```

**虚拟滚动（长列表）**

```tsx
import { FixedSizeList } from 'react-window';

const MessageList = ({ messages }) => (
  <FixedSizeList
    height={600}
    itemCount={messages.length}
    itemSize={80}
    width="100%"
  >
    {({ index, style }) => (
      <div style={style}>
        <MessageItem message={messages[index]} />
      </div>
    )}
  </FixedSizeList>
);
```

**图片优化**

```tsx
<img src={imageSrc} loading="lazy" alt="Description" />
```

### 12.7 测试策略

**单元测试**

```tsx
import { render, screen, fireEvent } from '@testing-library/react';
import { Button } from './Button';

describe('Button', () => {
  it('renders correctly', () => {
    render(<Button>Click me</Button>);
    expect(screen.getByText('Click me')).toBeInTheDocument();
  });

  it('calls onClick when clicked', () => {
    const handleClick = jest.fn();
    render(<Button onClick={handleClick}>Click me</Button>);
    fireEvent.click(screen.getByText('Click me'));
    expect(handleClick).toHaveBeenCalledTimes(1);
  });
});
```

**集成测试**

```tsx
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ChatPage } from './ChatPage';

describe('ChatPage', () => {
  it('sends a message when form is submitted', async () => {
    render(<ChatPage />);
    const input = screen.getByPlaceholderText('Type a message...');
    const sendButton = screen.getByRole('button', { name: /send/i });

    await userEvent.type(input, 'Hello, world!');
    await userEvent.click(sendButton);

    await waitFor(() => {
      expect(screen.getByText('Hello, world!')).toBeInTheDocument();
    });
  });
});
```

---

## 附录：资源引用

| 资源 | 路径/地址 |
|------|-----------|
| Logo 文件 | `/media_resources/jellyfishlogo.png` |
| 代码字体 | Google Fonts CDN: JetBrains Mono |
| 图标库 | `@phosphor-icons/react` (npm) |
| 主题配置 | `frontend/src/styles/theme.ts` |
| 全局样式 | `frontend/src/styles/global.css` |
| Chat CSS Module | `frontend/src/pages/Chat/chat.module.css` |

---

*JellyfishBot Design System v1.0 — Last updated: 2026-04-02*
