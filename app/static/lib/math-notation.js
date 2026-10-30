// Kept equivalent to app/math_notation.py; protected text remains verbatim.
function symbols(value) {
  return value.replace(/[−－＋＝＜＞]/g,c=>({'−':'-','－':'-','＋':'+','＝':'=','＜':'<','＞':'>'}[c]))
    .replace(/(?<![A-Za-z\\^])([A-Za-z\u0370-\u03ff]|\\(?:alpha|beta|gamma|delta|theta|phi|varphi|psi|omega))\s*((?:(?:\\prime\b|[′″])\s*)+)/g,(_,base,primes)=>{
      const count=(primes.match(/\\prime\b|′/g)||[]).length+(primes.match(/″/g)||[]).length*2;
      return base+'^{'+String.raw`\prime`.repeat(count)+'}';
    });
}

export function normaliseMathBody(value) {
  const protectedText=/\\(?:text(?:normal|rm|bf|it)?|textrm|textbf|textit|operatorname)\s*\{/g;
  let out='',start=0,match;
  while((match=protectedText.exec(value))){
    out+=symbols(value.slice(start,match.index));
    let end=protectedText.lastIndex,depth=1;
    while(end<value.length&&depth){if(value[end]==='\\'){end+=2;continue;}if(value[end]==='{')depth++;else if(value[end]==='}')depth--;end++;}
    out+=value.slice(match.index,end);start=end;protectedText.lastIndex=end;
  }
  return out+symbols(value.slice(start));
}
