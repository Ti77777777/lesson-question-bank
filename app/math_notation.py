"""Conservative repairs inside delimited mathematics; never rewrite prose."""
import re

SPANS = re.compile(r'(?<!\\)(?:\$(?!\$)([^$\n]+)\$|\$\$([\s\S]+?)\$\$|\\\(([\s\S]+?)\\\)|\\\[([\s\S]+?)\\\])')
TEXT = re.compile(r'\\(?:text(?:normal|rm|bf|it)?|textrm|textbf|textit|operatorname)\s*\{')
PRIMES = re.compile(r'(?<![A-Za-z\\^])([A-Za-z\u0370-\u03ff]|\\(?:alpha|beta|gamma|delta|theta|phi|varphi|psi|omega))\s*((?:(?:\\prime\b|[′″])\s*)+)')


def _symbols(value):
    def derivative(match):
        count = len(re.findall(r'\\prime\b|′', match[2])) + match[2].count('″') * 2
        return match[1] + '^{' + r'\prime' * count + '}'
    return PRIMES.sub(derivative, value.translate(str.maketrans('−－＋＝＜＞', '--+=<>')))


def normalise_math_body(value):
    out=[];start=0
    while match := TEXT.search(value,start):
        out.append(_symbols(value[start:match.start()]))
        end=match.end();depth=1
        while end<len(value) and depth:
            if value[end]=='\\':end+=2;continue
            if value[end]=='{':depth+=1
            elif value[end]=='}':depth-=1
            end+=1
        out.append(value[match.start():end]);start=end
    out.append(_symbols(value[start:]))
    return ''.join(out)


def normalise_math_notation(value):
    def segment(match):
        group=next(i for i in range(1,5) if match[i] is not None)
        a,b=match.start(group)-match.start(),match.end(group)-match.start()
        return match[0][:a]+normalise_math_body(match[group])+match[0][b:]
    return SPANS.sub(segment,value)
