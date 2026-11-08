'use client';
import { useEffect,useRef,useState } from 'react';
import type { KeyboardEvent } from 'react';
import { Check,ChevronDown } from 'lucide-react';

type Option={value:string;label:string};
type Props={label:string;value:string;options:Option[];onChange:(value:string)=>void;minWidth?:number};

export function FilterDropdown({label,value,options,onChange,minWidth}:Props){
 const [open,setOpen]=useState(false);
 const [highlight,setHighlight]=useState(0);
 const root=useRef<HTMLDivElement>(null);
 const button=useRef<HTMLButtonElement>(null);
 const menu=useRef<HTMLDivElement>(null);
 const selected=options.find(x=>x.value===value);
 useEffect(()=>{
  const off=(e:MouseEvent)=>{if(root.current&&!root.current.contains(e.target as Node))setOpen(false)};
  const key=(e:KeyboardEvent)=>{if(e.key==='Escape'){setOpen(false);button.current?.focus()}};
  document.addEventListener('mousedown',off);document.addEventListener('keydown',key);
  return()=>{document.removeEventListener('mousedown',off);document.removeEventListener('keydown',key)};
 },[]);
 useEffect(()=>{
  if(open){setHighlight(Math.max(0,options.findIndex(o=>o.value===value)))}
 },[open,options,value]);
 useEffect(()=>{
  if(open)menu.current?.querySelector<HTMLElement>(`[data-idx="${highlight}"]`)?.scrollIntoView({block:'nearest'});
 },[highlight,open]);
 const choose=(option:Option)=>{onChange(option.value);setOpen(false);button.current?.focus()};
 const keys=(e:KeyboardEvent)=>{
  if(['ArrowDown','ArrowUp','Home','End','Enter',' '].includes(e.key))e.preventDefault();
  if(!open&&(e.key==='ArrowDown'||e.key==='Enter'||e.key===' ')){setOpen(true);return;}
  if(!open)return;
  if(e.key==='ArrowDown')setHighlight(n=>(n+1)%options.length);
  if(e.key==='ArrowUp')setHighlight(n=>(n+options.length-1)%options.length);
  if(e.key==='Home')setHighlight(0);
  if(e.key==='End')setHighlight(options.length-1);
  if(e.key==='Enter'||e.key===' ')choose(options[highlight]);
 };
 return <div className="filter-root" ref={root} style={{minWidth}} onKeyDown={keys}>
  <button ref={button} type="button" className={'filter-button '+(open?'is-open':'')} onClick={()=>setOpen(x=>!x)}
   aria-haspopup="listbox" aria-expanded={open} aria-label={label}>
   <span className={value?'filter-selected':''}>{selected?.label||label}</span><ChevronDown size={16} className={open?'rotate180':''}/>
  </button>
  {open&&<div className="filter-menu" role="listbox" aria-label={label} ref={menu}>
   {options.map((option,i)=><button type="button" role="option" aria-selected={option.value===value}
     data-idx={i} key={option.value} className={'filter-option '+(highlight===i?'highlight':'')}
     onMouseEnter={()=>setHighlight(i)} onClick={()=>choose(option)}>
     <span>{option.label}</span>{option.value===value&&<Check size={15} className="text-accent"/>}
   </button>)}
  </div>}
 </div>
}
