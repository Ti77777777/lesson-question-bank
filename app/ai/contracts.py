"""Validation of the deliberately small AI interchange contract (not a schema engine)."""
import math
import re
from app.ai.provider import ProviderError

QTYPES = ('single', 'multi', 'blank', 'solve')
DIMENSIONS = ('knowledge', 'method', 'idea', 'prereq', 'purpose', 'error', 'textbook')
FORBIDDEN = re.compile(r'\\(?:input|include|includegraphics|openin|openout|read|write|immediate|special|catcode|csname|def|edef|gdef|xdef|let|futurelet|usepackage|documentclass|directlua|luaexec|shellescape|newcommand|renewcommand|newenvironment|href|url|verbatim|scantokens|endlinechar|every\w*|loop|repeat|shipout|pdf\w*|XeTeX\w*)\b', re.I)
ALLOWED=set('frac dfrac tfrac cfrac sqrt left right middle big Big bigg Bigg bigl bigr Bigl Bigr biggl biggr Biggl Biggr cdot cdots ldots vdots ddots times div pm mp le leq ge geq ne neq lt gt approx sim simeq equiv cong in notin ni subset supset subseteq supseteq subsetneq supsetneq nsubseteq nsupseteq subsetneqq supsetneqq cap cup setminus complement emptyset varnothing forall exists nexists neg lnot land lor wedge vee implies iff Rightarrow Leftarrow Leftrightarrow rightarrow leftarrow leftrightarrow to mapsto longrightarrow Longrightarrow longleftrightarrow Longleftrightarrow uparrow downarrow infty partial nabla sum prod coprod int iint iiint oint lim limsup liminf min max sup inf sin cos tan cot sec csc arcsin arccos arctan sinh cosh tanh log ln exp lg det gcd lcm deg dim ker arg mod bmod pmod binom dbinom tbinom choose over atop underbrace overbrace underline overline widehat widetilde hat bar vec dot ddot tilde acute grave breve check mathbb mathbf mathrm mathcal mathit mathsf mathtt boldsymbol bm text textrm textbf textit textnormal operatorname substack stackrel overset underset displaystyle textstyle scriptstyle scriptscriptstyle quad qquad enspace thinspace medspace thickspace colon mid vert Vert lvert rvert lVert rVert lbrace rbrace langle rangle lceil rceil lfloor rfloor backslash angle triangle perp parallel nparallel circ degree prime bot top ast star bullet odot oplus otimes propto therefore because ell aleph hbar Re Im wp i j alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu xi omicron pi varpi rho varrho sigma varsigma tau upsilon phi varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega begin end hline cline multicolumn arraystretch arraycolsep hspace vspace par newline linebreak item qbankimage mathop mathrel mathbin mathord mathpunct mathopen mathclose limits nolimits not rm bf tt cal'.split())
ENVIRONMENTS=set('cases aligned alignedat gathered matrix pmatrix bmatrix Bmatrix vmatrix Vmatrix smallmatrix array tabular equation equation* align align* gather gather* split enumerate itemize'.split())
ALLOWED.update('leqslant geqslant lesssim gtrsim ll gg prec succ preceq succeq notsubseteq textcircled'.split())
ALLOWED.add('frown')  # Standard math symbol, also used above letters to denote an arc.
ALLOWED.update(('dots','tag'))
ALLOWED.update('overrightarrow overleftarrow overleftrightarrow underrightarrow underleftarrow underleftrightarrow xrightarrow xleftarrow boxed mathfrak nleq ngeq nless ngtr square blacksquare diamond lozenge checkmark'.split())

CJK_MATH_PUNCT='、，。；：？！（）【】「」『』《》'
MATH_SEGMENT_PATTERN=r'(?<!\\)\\\[(.+?)(?<!\\)\\\]|(?<!\\)\$\$(.+?)(?<!\\)\$\$|(?<!\\)\$((?:\\.|[^\$\\])*?)(?<!\\)\$'

def fail(message):
    raise ProviderError('contract_invalid', message)

def math_segments(value):
    """Yield (body, display) for $…$, $$…$$ and \\[…\\]."""
    for match in re.finditer(MATH_SEGMENT_PATTERN,value,re.S):
        body=match.group(1) or match.group(2) or match.group(3) or ''
        yield body,bool(match.group(1) or match.group(2))

def dollars_balanced(value):
    r"""成对的 $ 才能被网页与 LaTeX 一致地解析；\$ 视为转义，不参与配对。"""
    count = 0
    i = 0
    while i < len(value):
        if value[i] == '\\' and i + 1 < len(value):
            i += 2
            continue
        if value[i] == '$':
            count += 1
        i += 1
    return count % 2 == 0


def normalise_math_punctuation(value):
    """Preserve punctuation as text in math mode, without changing its meaning."""
    def segment(match):
        group=next(i for i in (1,2,3) if match.group(i) is not None)
        body=match.group(group)
        # Do not wrap punctuation already protected by a text command again.
        parts=re.split(r'(\\(?:text|textrm|textbf|textit|operatorname)\{[^{}]*\})',body)
        for i in range(0,len(parts),2):
            parts[i]=re.sub('['+re.escape(CJK_MATH_PUNCT)+']+',lambda m:r'\text{'+m[0]+'}',parts[i])
        start=match.start(group)-match.start();end=match.end(group)-match.start()
        return match[0][:start]+''.join(parts)+match[0][end:]
    return re.sub(MATH_SEGMENT_PATTERN,segment,value,flags=re.S)

def math_issues(value):
    """排版问题：公式内再套 $…$、数学模式里夹中文标点。两者都会让 xelatex 报错或缺字。"""
    for body,display in math_segments(value):
        if re.search(r'(?<!\\)\$',body):
            yield r'公式里嵌套了 $…$，应删去内部定界符或改用\[…\]'
        visible=re.sub(r'\\(?:text|textrm|textbf|textit|operatorname)\{[^{}]*\}','',body)
        bad=''.join(sorted({c for c in visible if c in CJK_MATH_PUNCT}))
        if bad:yield '数学模式里出现中文标点 '+bad+'，应放进 \\text{} 或移到公式外'

def text(value, name, required=False):
    if not isinstance(value, str) or len(value) > 100000 or (required and not value.strip()):
        fail('AI 字段不符合要求：' + name)
    if FORBIDDEN.search(value) or '^^' in value or '\x00' in value:
        fail('AI 结果包含不允许的 TeX 指令：' + name)
    for command in re.findall(r'\\([A-Za-z]+|[^A-Za-z])',value):
        if command not in ALLOWED and command not in ('\\','{','}','[',']','(',')',',',';',':','!',' ','%','_','$','#','&','|'):
            fail('AI 结果包含未支持的 TeX 命令：'+command)
    for environment in re.findall(r'\\(?:begin|end)\{([^}]+)\}',value):
        if environment not in ENVIRONMENTS:fail('AI 结果包含不允许的 TeX 环境')
    for issue in math_issues(value):fail('AI 结果排版有问题：'+issue+'（'+name+'）')
    if not dollars_balanced(value):fail('AI 结果的 $ 不成对，网页与 PDF 会显示源码（'+name+'）')
    for path in re.findall(r'\\qbankimage\{([^}]+)\}',value):
        if not re.fullmatch(r'assets/[A-Za-z0-9_./-]+\.(?:png|jpg|jpeg|webp|pdf)',path,re.I) or '..' in path.split('/'):
            fail('AI题图路径无效')
    return value

def box(value):
    if not isinstance(value, list) or len(value) != 4 or any(type(v) not in (int,float) or not math.isfinite(v) for v in value):
        fail('图片定位坐标无效')
    if not (0 <= value[0] < value[2] <= 1 and 0 <= value[1] < value[3] <= 1):
        fail('图片定位超出页面范围')
    return value

def items(value, name, maximum=100):
    if not isinstance(value, list) or len(value) > maximum:fail('AI 列表无效：'+name)
    return value

def objects(value, name, maximum=100):
    rows=items(value,name,maximum)
    if any(not isinstance(row,dict) for row in rows):fail('AI 列表成员应为对象：'+name)
    return rows

def recognition_questions(result):
    if not isinstance(result,dict):fail('识题结果不是对象')
    return objects(result.get('questions'),'questions',40)

def question(q):
    if not isinstance(q,dict):fail('AI 题目格式无效')
    if 'continuation' in q and type(q['continuation']) is not bool:fail('续题标识应为布尔值')
    text(q.get('qno'),'qno',True)
    if q.get('qtype') not in QTYPES:fail('AI 题型无效')
    text(q.get('stem',''),'stem')
    points=q.get('total_points')
    if points is not None and (type(points) not in (float,int) or not math.isfinite(points) or not 0<=points<=150):fail('题目分值无效')
    keys=[]
    for o in items(q.get('options',[]),'options',8):
        if not isinstance(o,dict):fail('选项无效')
        k=text(o.get('key'),'option.key',True)
        if k not in ('A','B','C','D','E','F') or k in keys:fail('选项标识重复或无效')
        keys.append(k);text(o.get('text'),'option.text',True)
    if not q.get('continuation') and q['qtype'] in ('single','multi') and len(keys)<2:fail('选择题缺少选项')
    subkeys=[]
    for s in objects(q.get('subquestions',[]),'subquestions',20):
        k=text(s.get('key'),'subquestion.key',True);text(s.get('text'),'subquestion.text',True)
        if k in subkeys:fail('小问标识重复')
        subkeys.append(k)
        for dep in items(s.get('depends_on',[]),'depends_on',20):
            if not isinstance(dep,str) or dep==k:fail('小问依赖无效')
    for s in q.get('subquestions',[]):
        if any(d not in subkeys for d in s.get('depends_on',[])):fail('小问依赖不存在')
    dependencies={s['key']:s.get('depends_on',[]) for s in q.get('subquestions',[])}
    visiting=set();visited=set()
    def visit(key):
        if key in visiting:fail('小问依赖存在循环')
        if key in visited:return
        visiting.add(key)
        for dependency in dependencies[key]:visit(dependency)
        visiting.remove(key);visited.add(key)
    for key in subkeys:visit(key)
    if not q.get('continuation') and not q.get('stem','').strip():
        if q['qtype']=='solve' and subkeys:
            # All mathematical content may be in the subquestions themselves.
            q['stem']='解答下列问题。'
        else:text(q.get('stem',''),'stem',True)
    for field in ('statement_regions','reference_regions','figures'):
        regions=[]
        for r in items(q.get(field,[]),field,40):
            # 部分模型将 {bbox:[...]} 简写为 [...]，坐标含义完全相同。
            if isinstance(r,list):r={'bbox':r}
            if not isinstance(r,dict):fail('图片区域格式无效：'+field)
            value=r.get('bbox')
            # Equivalent named-edge replies need no extra paid recognition call.
            if isinstance(value,list) and len(value)==1 and isinstance(value[0],dict):value=value[0]
            if isinstance(value,dict) and set(value)=={'left','top','right','bottom'}:
                value=[value[k] for k in ('left','top','right','bottom')]
            r['bbox']=box(value)
            if 'key' in r:text(r['key'],field+'.key',True)
            regions.append(r)
        q[field]=regions
    ref=q.get('reference',{})
    if not isinstance(ref,dict):fail('参考解答格式无效')
    text(ref.get('answer',''),'reference.answer');text(ref.get('analysis',''),'reference.analysis')
    for uncertainty in items(q.get('uncertainties',[]),'uncertainties',40):
        text(uncertainty,'uncertainty',True)
    return q

def recognition(result):
    for q in recognition_questions(result):
        if type(q.get('qno'))==int:q['qno']=str(q['qno'])
        question(q)
    return result

def solved(result, source):
    if not isinstance(result,dict):fail('解答结果不是对象')
    if source.get('qtype') in ('single','multi') and not source.get('subquestions'):
        # Some models put option-by-option explanations in the subanswer transport
        # field. These are not original subquestions; full steps remain intact.
        extras=result.get('subanswers')
        option_keys={o['key'] for o in source.get('options',[])}
        if isinstance(extras,list) and extras and all(isinstance(a,dict) and a.get('key') in option_keys and isinstance(a.get('answer'),str) for a in extras):
            result['subanswers']=[]
    extras=result.get('subanswers')
    if not source.get('subquestions') and isinstance(extras,list) and len(extras)==1 and isinstance(extras[0],dict):
        # A numbered whole-question answer repeated verbatim is not a new subpart.
        only=extras[0]
        if source.get('qno') is not None and 'key' in only and str(only.get('key'))==str(source['qno']) and isinstance(only.get('answer'),str) and only['answer'].strip()==str(result.get('answer','')).strip():
            result['subanswers']=[]
    if result.get('status') not in ('solved','needs_input','uncertain'):fail('解答状态无效')
    text(result.get('answer',''),'answer',result.get('status')=='solved')
    keys=items(result.get('answer_keys',[]),'answer_keys',6)
    if any(not isinstance(k,str) for k in keys):fail('答案选项标识应为文本')
    valid=[x['key'] for x in source.get('options') or []]
    if len(set(keys))!=len(keys) or any(k not in valid for k in keys):fail('答案选项不存在')
    if source['qtype']=='single' and result['status']=='solved' and len(keys)!=1:fail('单选题答案数量无效')
    if source['qtype']=='multi' and result['status']=='solved' and not keys:fail('多选题缺少答案标识')
    sols=objects(result.get('solutions',[]),'solutions',4)
    if result['status']=='solved' and not sols:fail('缺少解析')
    for s in sols:
        for key in ('label','steps'):text(s.get(key),key,True)
        for key in ('approach','conditions','teacher_tip'):text(s.get(key,''),key)
    subkeys=[s['key'] for s in source.get('subquestions') or []]
    answered=set()
    for s in objects(result.get('subanswers',[]),'subanswers',20):
        key=text(s.get('key'),'subanswer.key',True)
        if key not in subkeys:fail('解答小问不属于原题')
        if key in answered:fail('小问解答标识重复')
        answered.add(key)
        text(s.get('answer'),'subanswer',True)
    if result['status']=='solved' and set(subkeys)!=set(s['key'] for s in result.get('subanswers',[])):fail('小问解答不完整')
    return result

def reviewed(result):
    if not isinstance(result,dict):fail('复核结果不是对象')
    if result.get('verdict') not in ('no_issue_found','needs_review','conflict','needs_input'):fail('复核结论无效')
    text(result.get('reason'),'review.reason',True)
    for tag in objects(result.get('tags',[]),'tags',30):
        if tag.get('dimension') not in DIMENSIONS:fail('标签维度无效')
        text(tag.get('name'),'tag.name',True)
        if len(tag['name'])>80:fail('标签名过长')
        text(tag.get('reason',''),'tag.reason')
    d=result.get('difficulty')
    if not isinstance(d,dict) or any(type(d.get(k))!=int or not 1<=d[k]<=5 for k in ('calc','think','scope')):fail('难度评分应为1至5')
    for k in ('level','note'):text(d.get(k,''),'difficulty.'+k)
    if result.get('rec_level') not in ('S','A','B','C'):fail('推荐等级无效')
    text(result.get('rec_reason',''),'rec_reason')
    text(result.get('reference_comparison',''),'reference_comparison')
    if not isinstance(result.get('teaching',{}),dict):fail('教学建议格式无效')
    for value in result.get('teaching',{}).values():
        if not isinstance(value,str):fail('教学建议应为文本')
        text(value,'teaching')
    n=result.get('time_solve_min')
    if type(n) not in (float,int) or not math.isfinite(n) or not 0<n<=300:fail('预计用时无效')
    return result
