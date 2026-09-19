// A small, safe math-expression evaluator (no eval / Function). Compiles an
// expression string into RPN once via the shunting-yard algorithm, then
// evaluates it against a variable-bindings object. Supports + - * / ^, unary
// minus, parentheses, common functions and the constants pi/e. Used by the
// `math` view (plotting) and the `expr` runtime.

const FUNCTIONS = {
  sin: Math.sin, cos: Math.cos, tan: Math.tan, asin: Math.asin, acos: Math.acos,
  atan: Math.atan, sinh: Math.sinh, cosh: Math.cosh, tanh: Math.tanh,
  exp: Math.exp, log: Math.log, log10: Math.log10, log2: Math.log2,
  sqrt: Math.sqrt, abs: Math.abs, floor: Math.floor, ceil: Math.ceil,
  round: Math.round, sign: Math.sign,
  min: Math.min, max: Math.max, pow: Math.pow, atan2: Math.atan2,
};
const CONSTANTS = { pi: Math.PI, e: Math.E, tau: 2 * Math.PI };
const OPS = {
  '+': { prec: 2, fn: (a, b) => a + b },
  '-': { prec: 2, fn: (a, b) => a - b },
  '*': { prec: 3, fn: (a, b) => a * b },
  '/': { prec: 3, fn: (a, b) => a / b },
  '%': { prec: 3, fn: (a, b) => a % b },
  '^': { prec: 4, right: true, fn: (a, b) => Math.pow(a, b) },
};

function tokenize(src) {
  const tokens = [];
  const re = /\s*([A-Za-z_]\w*|\d+\.?\d*|\.\d+|[()+\-*/%^,])/g;
  let m;
  let last = null;
  while ((m = re.exec(src)) !== null) {
    let t = m[1];
    // unary minus → sentinel 'u'
    if (t === '-' && (last === null || last === '(' || last === ',' || OPS[last])) t = 'u';
    tokens.push(t);
    last = t === 'u' ? '-' : t;
  }
  return tokens;
}

// Compile once → returns a function(bindings) => number.
export function compile(src) {
  const tokens = tokenize(String(src || ''));
  const output = [];
  const stack = [];
  for (const t of tokens) {
    if (/^(\d|\.)/.test(t)) output.push({ num: parseFloat(t) });
    else if (t in FUNCTIONS) stack.push({ fn: t });
    else if (/^[A-Za-z_]\w*$/.test(t)) output.push({ name: t });
    else if (t === ',') { while (stack.length && stack[stack.length - 1] !== '(') output.push(stack.pop()); }
    else if (t === 'u') stack.push({ unary: true });
    else if (t in OPS) {
      while (stack.length) {
        const top = stack[stack.length - 1];
        if (top === '(' || top.fn || top.unary) break;
        const o = OPS[top.op];
        if (o && (o.prec > OPS[t].prec || (o.prec === OPS[t].prec && !OPS[t].right))) output.push(stack.pop());
        else break;
      }
      stack.push({ op: t });
    } else if (t === '(') stack.push('(');
    else if (t === ')') {
      while (stack.length && stack[stack.length - 1] !== '(') output.push(stack.pop());
      stack.pop();
      if (stack.length && stack[stack.length - 1].fn) output.push(stack.pop());
    }
  }
  while (stack.length) output.push(stack.pop());

  return function evaluate(bindings) {
    const s = [];
    for (const node of output) {
      if (node.num !== undefined) s.push(node.num);
      else if (node.name) {
        const v = bindings[node.name] ?? CONSTANTS[node.name];
        s.push(v === undefined ? NaN : v);
      } else if (node.unary) s.push(-s.pop());
      else if (node.fn) {
        const fn = FUNCTIONS[node.fn];
        // most fns are unary; min/max/pow/atan2 are binary
        if (fn.length >= 2) { const b = s.pop(); const a = s.pop(); s.push(fn(a, b)); }
        else s.push(fn(s.pop()));
      } else if (node.op) { const b = s.pop(); const a = s.pop(); s.push(OPS[node.op].fn(a, b)); }
    }
    return s.length ? s[s.length - 1] : NaN;
  };
}

export function safeEval(src, bindings) {
  try { return compile(src)(bindings || {}); } catch { return NaN; }
}
