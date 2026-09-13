using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text.RegularExpressions;
using System.Windows.Forms;
namespace AspPwPanelDesktop {
  internal sealed class MainForm : Form {
    readonly Color Navy=Color.FromArgb(12,19,32), Blue=Color.FromArgb(77,142,255), Gold=Color.FromArgb(216,179,106);
    readonly string root; AppSettings settings; TextBox server,port,user,url; Label status;
    public MainForm(){root=Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"..","..")); settings=AppSettings.Load(); Text="ASP PWPanel Desktop 1.0.0"; Size=new Size(820,570); MinimumSize=new Size(760,520); StartPosition=FormStartPosition.CenterScreen; Font=new Font("Segoe UI",9F); BackColor=Color.FromArgb(243,246,250); Build(); LoadValues();}
    void Build(){var head=new Panel{Dock=DockStyle.Top,Height=86,BackColor=Navy}; head.Controls.Add(new Label{Text="ASP PWPanel Desktop",ForeColor=Color.White,Font=new Font("Segoe UI Semibold",22F),AutoSize=true,Location=new Point(28,14)}); head.Controls.Add(new Label{Text="Independent installer & updater • Installer dan updater mandiri",ForeColor=Color.FromArgb(170,184,205),AutoSize=true,Location=new Point(31,55)}); Controls.Add(head);
      var title=new Label{Text="Install or update ASP PWPanel",Font=new Font("Segoe UI Semibold",17F),ForeColor=Navy,AutoSize=true,Location=new Point(30,116)}; Controls.Add(title);
      Controls.Add(new Label{Text="This application manages PWPanel only. It does not install, update, or remove ASP CPW.",ForeColor=Color.DimGray,AutoSize=true,Location=new Point(32,153)});
      server=Field("Ubuntu address",32,205,340); port=Field("SSH port",398,205,120); user=Field("SSH username",544,205,210); url=Field("Panel URL",32,285,722);
      var save=ButtonOf("Save Settings",32,368,160,Navy); save.Click+=(s,e)=>Save(); var test=ButtonOf("Test SSH",208,368,135,Gold); test.Click+=(s,e)=>RunSshTest(); var install=ButtonOf("Install / Update",359,368,190,Blue); install.Click+=(s,e)=>Install(); var open=ButtonOf("Open Panel",565,368,189,Navy); open.Click+=(s,e)=>OpenPanel(); Controls.Add(save);Controls.Add(test);Controls.Add(install);Controls.Add(open);
      status=new Label{Text="Ready. Passwords are requested by SSH and are never stored.",ForeColor=Color.DimGray,AutoSize=false,Location=new Point(32,438),Size=new Size(722,55)};Controls.Add(status);
    }
    TextBox Field(string label,int x,int y,int w){Controls.Add(new Label{Text=label,ForeColor=Navy,AutoSize=true,Location=new Point(x,y-23)});var box=new TextBox{Location=new Point(x,y),Width=w,Height=28};Controls.Add(box);return box;}
    Button ButtonOf(string text,int x,int y,int w,Color color){return new Button{Text=text,Location=new Point(x,y),Size=new Size(w,44),BackColor=color,ForeColor=Color.White,FlatStyle=FlatStyle.Flat};}
    void LoadValues(){server.Text=settings.Server;port.Text=settings.Port;user.Text=settings.User;url.Text=settings.PanelUrl;}
    bool Save(){int p;Uri uri;string host=server.Text.Trim(), login=user.Text.Trim();bool validHost=Regex.IsMatch(host,@"^[A-Za-z0-9._:-]+$");bool validLogin=Regex.IsMatch(login,@"^[a-z_][a-z0-9_-]{0,31}$");if(!validHost||!validLogin||!int.TryParse(port.Text,out p)||p<1||p>65535||!Uri.TryCreate(url.Text,UriKind.Absolute,out uri)||(uri.Scheme!="http"&&uri.Scheme!="https")){MessageBox.Show("Complete valid SSH settings and an HTTP/HTTPS Panel URL first.","Settings",MessageBoxButtons.OK,MessageBoxIcon.Warning);return false;} settings.Server=host;settings.Port=port.Text.Trim();settings.User=login;settings.PanelUrl=url.Text.Trim();settings.Save();status.Text="Settings saved. No password was stored.";return true;}
    void StartPowerShell(string arguments,string message){var psi=new ProcessStartInfo("powershell.exe",arguments){UseShellExecute=true,WorkingDirectory=root};Process.Start(psi);status.Text=message;}
    void Install(){if(!Save())return;string script=Path.Combine(root,"installer","install-from-windows.ps1");if(!File.Exists(script)){MessageBox.Show("Installer script not found: "+script);return;} StartPowerShell("-NoProfile -ExecutionPolicy Bypass -NoExit -File \""+script+"\" -Server \""+settings.Server+"\" -Port "+settings.Port+" -User \""+settings.User+"\"","Installation opened in a secure console. Enter the Ubuntu password there.");}
    void RunSshTest(){if(!Save())return;StartPowerShell("-NoProfile -NoExit -Command \"ssh -p "+settings.Port+" '"+settings.User+"@"+settings.Server+"' 'echo ASP-PWPANEL-SSH-OK'\"","SSH connection test opened.");}
    void OpenPanel(){if(!Save())return;Process.Start(new ProcessStartInfo(settings.PanelUrl){UseShellExecute=true});}
  }
}
