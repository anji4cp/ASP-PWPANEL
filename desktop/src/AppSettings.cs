using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
namespace AspPwPanelDesktop {
  internal sealed class AppSettings {
    public string Server="127.0.0.1", Port="2223", User="pwadmin", PanelUrl="http://127.0.0.1:8081/";
    private static string FileName { get { return Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "ASP Editor Studio", "PWPanel Desktop", "settings.ini"); } }
    public static AppSettings Load() { var s=new AppSettings(); try { if(!File.Exists(FileName)) return s; foreach(var line in File.ReadAllLines(FileName)){int p=line.IndexOf('='); if(p<1) continue; string v=Encoding.UTF8.GetString(Convert.FromBase64String(line.Substring(p+1))); switch(line.Substring(0,p)){case "Server":s.Server=v;break;case "Port":s.Port=v;break;case "User":s.User=v;break;case "PanelUrl":s.PanelUrl=v;break;}} } catch{} return s; }
    public void Save(){Directory.CreateDirectory(Path.GetDirectoryName(FileName)); File.WriteAllLines(FileName,new[]{Line("Server",Server),Line("Port",Port),Line("User",User),Line("PanelUrl",PanelUrl)},Encoding.UTF8);}
    private static string Line(string k,string v){return k+"="+Convert.ToBase64String(Encoding.UTF8.GetBytes(v??""));}
  }
}
