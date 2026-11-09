import type {NextConfig} from "next";
const config: NextConfig = {output:process.env.STATIC_EXPORT==="true"?"export":"standalone", async rewrites(){if(process.env.STATIC_EXPORT==="true")return [];return [{source:"/api/:path*",destination:(process.env.BACKEND_URL || "http://127.0.0.1:8000")+"/api/:path*"}]}};
export default config;
