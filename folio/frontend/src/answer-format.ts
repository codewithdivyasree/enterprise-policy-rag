/** Hide evidence annotations without damaging prose or literal code examples. */
export function cleanAnswer(text:string):string {
 const sections=text.split(/(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`)/g);
 return sections.map((part,index)=>{
  if(index%2)return part;
  const subject=(offset:number,original:string,plural=false)=>{
   const before=original.slice(0,offset).trimEnd();
   return (!before||/[.!?\n]$/.test(before)?'The ':'the ')+(plural?'documents ':'document ');
  };
  // Markers used as subjects must keep a grammatical subject. Ordinary trailing
  // annotations are removed below. No factual clauses are removed or invented.
  part=part.replace(/\[S\d+\](?:\s*,\s*\[S\d+\])*(?:\s+and\s+\[S\d+\])\s+(?=(?:provide|confirm|state|support|explain|show|describe|indicate|establish)\b)/g,
    (match,offset,original)=>subject(offset,original,true));
  part=part.replace(/\[S\d+\]\s+(?=(?:provides|confirms|states|supports|explains|shows|describes|indicates|establishes)\b)/g,
    (match,offset,original)=>subject(offset,original));
  return part.replace(/\[S\d+\]/g,'').replace(/[ \t]+([.,;:!?])/g,'$1').replace(/[ \t]{2,}/g,' ');
 }).join('').trim();
}
