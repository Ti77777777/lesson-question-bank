/* 标签浏览目录。只组织展示，题目关联与标签原名仍使用原始 ID。 */
export const TAG_CATEGORIES = [
  ['knowledge', '知识点', '按数学内容定位本节课的考点'],
  ['method', '解题方法', '按解题步骤与常用工具选题'],
  ['idea', '数学思想', '关注转化、结构与思维方式'],
  ['prereq', '前置知识', '找到完成题目需要的基础'],
  ['error', '易错点', '围绕学生容易出错的环节选题'],
  ['purpose', '教学用途', '区分诊断、巩固、综合与迁移'],
  ['textbook', '教材位置', '按已有教材与册别标签定位'],
];

const SUBJECTS = [
  ['集合', /集合|Ω集|子集|交集|并集|补集|容斥/],
  ['常用逻辑用语', /逻辑|命题|量词|充分|必要|充要/],
  ['不等式', /不等式/],
  ['函数与导数', /函数|导数|极值|单调|零点/],
  ['方程与代数基础', /方程|因式|分式|分母|根式|指数运算|倒数|平方/],
  ['三角函数与解三角形', /三角|正弦|余弦|正切|弧度/],
  ['平面向量', /向量|数量积/],
  ['数列', /数列|等差|等比|递推/],
  ['解析几何', /圆锥曲线|椭圆|双曲线|抛物线|直线与圆|解析几何/],
  ['立体几何', /立体|空间|棱锥|棱柱|二面角|体积/],
  ['概率与统计', /概率|统计|随机|分布|方差|回归/],
  ['计数与组合', /计数|排列|组合|二项式/],
  ['模型与应用', /模型|应用|平均价格/],
];
const RULES = {
  knowledge: SUBJECTS,
  prereq: SUBJECTS,
  method: [
    ['图形与直观', /数形|数轴|韦恩|图像|图象|几何|对称/],
    ['分类与分段', /分类|奇偶|分段/],
    ['特值与反例', /特值|反例|排除/],
    ['换元与代数变形', /换元|代换|配凑|和积|通分|因式|由根设|方程转化|作差/],
    ['区间与函数分析', /判别式|导数|恒成立|端点/],
    ['元素分析与计数', /计数|配对|归属|列举/],
    ['逻辑推理', /量词|取反|否结论/],
  ],
  idea: [
    ['转化与化归', /化归|转化|补集/],
    ['数形结合', /数形/],
    ['数学建模', /建模|平均单价/],
    ['最值与等号', /等号|最值/],
    ['结构与计数', /分类|极端|计数|配对|端点|余数|模\s*1/],
  ],
  error: [
    ['逻辑与否定', /量词|否定|命题/],
    ['空集与边界', /端点|空集|真子集/],
    ['等号与最值', /(?:^|[^不])等号|取等|最小值|下确界/],
    ['条件与定义域', /定义域|分母|乘零|提价|正根|范围/],
    ['集合概念与关系', /集合|交集|并集|包含|互异|配对|setminus/],
    ['运算与符号', /符号|不等号|变号|同向|平方|判别式|系数/],
    ['建模与单位', /单位|费用|金额/],
  ],
  purpose: [
    ['诊断与辨析', /诊断|辨析/],
    ['基础与巩固', /基础|巩固|复习/],
    ['综合与迁移', /综合|迁移|新定义/],
    ['应用与建模', /应用|建模/],
    ['教学范围', /范围/],
  ],
  textbook: [
    ['必修第一册', /必修[第\s]*[一1]册/],
    ['必修第二册', /必修[第\s]*[二2]册/],
    ['选择性必修第一册', /选择性必修[第\s]*[一1]册/],
    ['选择性必修第二册', /选择性必修[第\s]*[二2]册/],
    ['选择性必修第三册', /选择性必修[第\s]*[三3]册/],
  ],
};
const FALLBACK = {knowledge: '其他知识点', method: '其他解题方法', idea: '其他数学思想', prereq: '其他前置知识', error: '其他易错点', purpose: '其他教学用途', textbook: '其他教材位置'};

export function classifyTag(tag) {
  const name = tag.name || '';
  // 册别和三角函数的名称存在包含关系，较具体的类别先匹配。
  let rules = RULES[tag.dimension] || [];
  if (tag.dimension === 'textbook') rules = [...rules.slice(2), ...rules.slice(0, 2)];
  if (['knowledge', 'prereq'].includes(tag.dimension) && /三角函数/.test(name)) return '三角函数与解三角形';
  return rules.find(([, pattern]) => pattern.test(name))?.[0] || FALLBACK[tag.dimension] || '其他标签';
}

export function makeTagDirectory(tags) {
  const seen = new Set();
  const available = tags.filter(tag => {
    if (!tag?.id || seen.has(tag.id) || tag.merged_into || tag.status === 'inactive') return false;
    seen.add(tag.id); return true;
  });
  const byId = new Map(available.map(t => [t.id, t]));
  const parents = new Set(available.map(t => t.parent_id).filter(Boolean));
  return available.map(tag => {
    let ancestor = tag, visited = new Set([tag.id]), cycle = false;
    while (ancestor.parent_id && byId.has(ancestor.parent_id)) {
      const parent = byId.get(ancestor.parent_id);
      if (parent.dimension !== tag.dimension) break;
      if (visited.has(parent.id)) { cycle = true; break; }
      visited.add(parent.id); ancestor = parent;
    }
    // 教师已设置的父级优先；没有父级的历史标签按名称归入浏览主题。
    const topic = !cycle && (ancestor.id !== tag.id || parents.has(tag.id)) ? ancestor.name : classifyTag(tag);
    return {tag, dimension: TAG_CATEGORIES.some(c => c[0] === tag.dimension) ? tag.dimension : 'other', topic};
  }).sort((a, b) => {
    const dimensionRank = dimension => {
      const rank = TAG_CATEGORIES.findIndex(c => c[0] === dimension);
      return rank < 0 ? 99 : rank;
    };
    const topicRank = entry => {
      const rank = (RULES[entry.dimension] || []).findIndex(r => r[0] === entry.topic);
      return rank < 0 ? 99 : rank;
    };
    return dimensionRank(a.dimension) - dimensionRank(b.dimension)
      || topicRank(a) - topicRank(b) || a.topic.localeCompare(b.topic, 'zh-CN')
      || a.tag.name.localeCompare(b.tag.name, 'zh-CN');
  });
}

export function filterTagDirectory(directory, {dimension = '', topic = '', keyword = '', selectedOnly = false, chosen = new Set()} = {}) {
  const term = keyword.trim().toLocaleLowerCase();
  return directory.filter(entry => (!dimension || entry.dimension === dimension)
    && (!topic || entry.topic === topic)
    && (!selectedOnly || chosen.has(entry.tag.id))
    && (!term || `${entry.tag.name || ''} ${entry.tag.definition || ''} ${entry.topic} ${TAG_CATEGORIES.find(c => c[0] === entry.dimension)?.[1] || ''}`.toLocaleLowerCase().includes(term)));
}
