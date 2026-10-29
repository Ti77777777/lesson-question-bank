/* 术语与状态字典：界面中文统一从这里取，避免各页写法不一。 */

export const QTYPES = { single: '单选', multi: '多选', blank: '填空', solve: '解答' };
export const QTYPES_SHORT = { single: '单选', multi: '多选', blank: '填空', solve: '解答' };

export const REVIEW_ITEMS = [
  ['stem', '题面'],
  ['answer', '答案'],
  ['solution', '解析'],
  ['figure', '图形'],
  ['classification', '分类'],
  ['boundary', '边界'],
  ['method_scope', '方法范围'],
];

export const REVIEW_RESULTS = {
  pending: '待审',
  pass: '通过',
  fail: '需修改',
  dispute: '有争议',
  na: '不适用',
};

export const REVIEW_TONE = {
  pass: 'pass',
  pending: 'pending',
  fail: 'fail',
  dispute: 'dispute',
  na: 'na',
  none: 'neutral',
};

export const ROLES = {
  diagnostic: '诊断',
  example: '例题',
  variant: '变式',
  practice: '跟练',
  homework: '课后巩固',
  summary: '小结',
  challenge: '挑战',
};

export const SECTION_KINDS = {
  question: '题目',
  concept: '概念回顾',
  summary: '方法小结',
  note: '教师提示',
};

export const REL_TYPES = {
  cond_variant: '条件变式',
  concl_variant: '结论变式',
  param: '参数化',
  generalize: '推广',
  special: '特殊情形',
  inverse: '逆向问题',
  same_method: '同法异题',
  error_contrast: '易错对比',
  correction: '纠错改编',
};

export const TAG_DIMENSIONS = {
  knowledge: '知识点',
  textbook: '教材位置',
  prereq: '前置知识',
  method: '解题方法',
  idea: '数学思想',
  purpose: '教学用途',
  error: '错误类型',
};

export const SOURCE_KINDS = {
  exam: '试卷',
  textbook: '教材',
  workbook: '教辅',
  lecture: '讲义',
  self: '自编',
  other: '其他',
};

export const EXAM_TYPES = {
  gaokao: '高考真题', mock: '模拟考', competition: '学科竞赛',
  monthly: '月考试卷', midterm: '期中考', final: '期末考',
  opening: '开学考', stage: '阶段测试', exam: '其他试卷',
};
export const SOURCE_GRADES = {senior1: '高一', senior2: '高二', senior3: '高三'};

export const INBOX_STATUS = {
  pending: '待提取',
  extracted: '已提取',
  organized: '已整理',
  rejected: '不采用',
};

export const INBOX_TONE = {
  pending: 'pending',
  extracted: 'info',
  organized: 'pass',
  rejected: 'na',
};

export const COLLECTION_PURPOSES = {
  handout: '讲义',
  quiz: '小测',
  homework: '作业',
  diagnostic: '诊断',
};

export const USAGE_PURPOSES = {
  class: '课堂',
  homework: '作业',
  quiz: '小测',
  review: '复习',
  other: '其他',
};

export const AUDIENCE_KINDS = {
  class: '班级',
  group: '分组',
  student: '学生',
};

export const ASSIGNMENT_STATUS = {
  planned: '计划中',
  distributed: '已发放',
  closed: '已结束',
  cancelled: '已取消',
};

export const ASSIGNMENT_TONE = {
  planned: 'pending',
  distributed: 'info',
  closed: 'na',
  cancelled: 'na',
};

export const RELEASE_STATUS = {
  built: '已生成',
  failed: '构建失败',
  archived: '已归档',
};

export const RELEASE_TONE = {
  built: 'pass',
  failed: 'fail',
  archived: 'na',
};

export const REC_LEVELS = ['S', 'A', 'B', 'C'];

export const HINT_LEVELS = {
  none: '不给提示',
  keyword: '关键词',
  method: '方法名',
  full: '完整提示',
};

export const BUILD_THEMES = [
  ['blue', '靛蓝'],
  ['green', '墨绿'],
  ['purple', '紫'],
  ['orange', '赭橙'],
  ['intj', '深蓝灰'],
  ['entp', '深红'],
];

export const BUILD_FORMATS = [
  ['standard', 'A4 标准版'],
  ['compact', '紧凑版'],
  ['loose', '宽松版'],
  ['single', '单页版'],
];

export const REVIEW_ITEM_LABEL = Object.fromEntries(REVIEW_ITEMS);

export function labelOf(dict, key, fallback = '') {
  if (key === null || key === undefined || key === '') return fallback;
  return dict[key] || String(key);
}

export function reviewLabel(result) {
  return REVIEW_RESULTS[result] || '待审';
}

export function reviewTone(result) {
  return REVIEW_TONE[result] || 'neutral';
}
