import type { Metadata } from 'next';
import './globals.css';
export const metadata:Metadata={title:'DataPulse — Data Engineering Jobs in Germany',
 description:'Find working student, junior and mid-level Data Engineering roles across Germany, directly from employer career portals.'};
export default function RootLayout({children}:{children:React.ReactNode}){
 return <html lang="en"><body>{children}</body></html>;
}
