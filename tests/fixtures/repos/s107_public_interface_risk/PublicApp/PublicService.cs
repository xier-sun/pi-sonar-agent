using System.Threading.Tasks;

namespace PublicApp
{
    public class PublicService : Interfaces.IPublicService
    {
        public async Task Run(string a, string b, string c, string d, string e, string f, string g, string h)
        {
            await Task.Delay(1);
        }
    }
}
