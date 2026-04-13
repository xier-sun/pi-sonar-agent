using Infrastructure;
using Infrastructure.Cache;
using Infrastructure.Extensions;
using Infrastructure.Utilities;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using Newtonsoft.Json;
using Npoi.Mapper;
using NStandard;
using OpenAuth.App.CommonHelp;
using OpenAuth.App.CommonHelp.Interfaces;
using OpenAuth.App.Finance.Interfaces;
using OpenAuth.App.Finance.Request;
using OpenAuth.App.Finance.Request.IDCReq;
using OpenAuth.App.Finance.Response;
using OpenAuth.App.Interface;
using OpenAuth.App.Order;
using OpenAuth.App.Order.Interface;
using OpenAuth.App.Response;
using OpenAuth.App.SystemApp.CategoryExtension;
using OpenAuth.Repository.Domain;
using OpenAuth.Repository.Domain.Sap;
using OpenAuth.Repository.Domain.Settlement;
using OpenAuth.Repository.Domain.Wms;
using OpenAuth.Repository.Interface;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using UglyToad.PdfPig.DocumentLayoutAnalysis.WordExtractor;

namespace OpenAuth.App.Finance
{
    /// <summary>
    /// 看板
    /// </summary>
    public class FinanceHomeApp : OnlyUnitWorkBaeApp , IFinanceHomeApp
    {
        private readonly CacheContextBase cacheContext;
        private readonly IHttpClientHelper httpClientHelper;
        private readonly IConfiguration configuration;
        private readonly IUserDepartMsgHelp userDepartMsgHelp;
        private readonly IOrderCommonApp orderCommonApp;
        private readonly ITaxReportApp taxReportApp;
        private readonly ServiceBaseApp serviceBaseApp;
        private const string managerAudit = "总经理审批";
        private const string view_all = "view_all";
        private const string view_dept = "view_dept";
        private const string view_self = "view_self";
        /// <summary>
        /// 构造函数
        /// </summary>
        /// <param name="_cacheContext"></param>
        /// <param name="_unitWork"></param>
        /// <param name="_auth"></param>
        /// <param name="_httpClientHelper"></param>
        /// <param name="_configuration"></param>
        /// <param name="_userDepartMsgHelp"></param>
        /// <param name="_orderCommonApp"></param>
        /// <param name="_taxReportApp"></param>
        /// <param name="_serviceBaseApp"></param>
        public FinanceHomeApp(CacheContextBase _cacheContext, IUnitWork _unitWork, IAuth _auth, IHttpClientHelper _httpClientHelper , IConfiguration _configuration, IUserDepartMsgHelp _userDepartMsgHelp, IOrderCommonApp _orderCommonApp, ITaxReportApp _taxReportApp, ServiceBaseApp _serviceBaseApp) : base (_unitWork, _auth) 
        {
             cacheContext = _cacheContext;
            httpClientHelper = _httpClientHelper;
            configuration = _configuration;
            userDepartMsgHelp = _userDepartMsgHelp;
            orderCommonApp = _orderCommonApp;
            taxReportApp = _taxReportApp;
            serviceBaseApp = _serviceBaseApp;
        }


        /// <summary>
        /// 获取节点直通率看板
        /// </summary>
        /// <param name="dto"></param>
        /// <returns></returns>
        public async Task<FinanceCardDto> GetFinanceCard(FinanceCardRequestDto dto)
        {
            var financeCardDto = new FinanceCardDto();
            financeCardDto.CardTitle = "节点直通率";
            financeCardDto.CardDatas = new List<FinanceCardDataDto>();
            dto.EndTime = dto.EndTime + TimeSpan.FromDays(1);
            if(dto.cardOrderType == CardOrderType.All)
            {
                var reimburseCardDetailDatas = await GetReimburseCard(dto);
                var outSourcCardDetailDatas = await GetOutSourcCard(dto);
                var wmsPurchasePayment = await GetWmsPayPurchaseCard(dto);
                var totalCardDetailDatas = reimburseCardDetailDatas.Union(outSourcCardDetailDatas).Union(wmsPurchasePayment);
                var resultCards = totalCardDetailDatas.GroupBy(x => x.StageName)
                    .Select(x => new FinanceCardDataDto
                    {
                        StageName = x.Key,
                        TargetDuration = dto.TargetHour,
                        TargetCount = x.Sum(x => x.TargetCount),
                        TotalCount = x.Sum(x=>x.TotalCount),
                        AchievingRate = Math.Round(x.Sum(x => x.TargetCount) * 0.1D / x.Sum(x => x.TotalCount) * 1000)
                    });
                financeCardDto.CardDatas.AddRange(resultCards);
            }
            else if(dto.cardOrderType == CardOrderType.ReimBurse)
            {
                var reimburseCardDatas = await GetReimburseCard(dto);
                financeCardDto.CardDatas.AddRange(reimburseCardDatas.Select(x => new FinanceCardDataDto
                {
                    StageName = x.StageName,
                    TargetDuration = dto.TargetHour,
                    TargetCount = x.TargetCount,
                    TotalCount = x.TotalCount,
                    AchievingRate = x.AchievingRate
                }));
            }
            else if(dto.cardOrderType == CardOrderType.OutSourc)
            {
                var reimburseCardDetailDatas = await GetOutSourcCard(dto);
                financeCardDto.CardDatas.AddRange(reimburseCardDetailDatas.Select(x => new FinanceCardDataDto
                {
                    StageName = x.StageName,
                    TargetDuration = dto.TargetHour,
                    TargetCount = x.TargetCount,
                    TotalCount = x.TotalCount,
                    AchievingRate = x.AchievingRate
                }));
            }
            else if(dto.cardOrderType == CardOrderType.PurchasePayment)
            {
                var wmsPurchasePaymentCardDetailDatas = await GetWmsPayPurchaseCard(dto);
                financeCardDto.CardDatas.AddRange(wmsPurchasePaymentCardDetailDatas.Select(x => new FinanceCardDataDto
                {
                    StageName = x.StageName,
                    TargetDuration = dto.TargetHour,
                    TargetCount = x.TargetCount,
                    TotalCount = x.TotalCount,
                    AchievingRate = x.AchievingRate
                }));
            }
            else //其他的还没有数据
            {
                var firstAuditCardData = new FinanceCardDataDto { StageName = "财务初审" , TargetDuration = dto.TargetHour, TargetCount = 0 , TotalCount = 0, AchievingRate = 0 };
                var secondAuditCardData = new FinanceCardDataDto { StageName = "财务复审", TargetDuration = dto.TargetHour, TargetCount = 0, TotalCount = 0, AchievingRate = 0 };
                var managerAuditCardData = new FinanceCardDataDto { StageName = managerAudit, TargetDuration = dto.TargetHour, TargetCount = 0, TotalCount = 0, AchievingRate = 0 };
                var receiveAndPayAuditCardData = new FinanceCardDataDto { StageName = "出纳", TargetDuration = dto.TargetHour, TargetCount = 0, TotalCount = 0, AchievingRate = 0 };
                financeCardDto.CardDatas.Add(firstAuditCardData);
                financeCardDto.CardDatas.Add(secondAuditCardData);
                financeCardDto.CardDatas.Add(managerAuditCardData);
                financeCardDto.CardDatas.Add(receiveAndPayAuditCardData);
            }
            return financeCardDto;
        }

        /// <summary>
        /// 获取销售业绩看板
        /// </summary>
        /// <param name="dto"></param>
        /// <returns></returns>
        public async Task<GetSaleBoundsCardResp> GetSaleBoundsCard(GetSaleBoundsCardReq dto)
        {
            var result = new GetSaleBoundsCardResp();
            var idcToken = await httpClientHelper.GetIDCToken();
            dto.BeginTime = new DateTime(dto.BeginTime.Year, dto.BeginTime.Month, 1, 0, 0, 0, dto.BeginTime.Kind);
            dto.EndTime = new DateTime(dto.EndTime.Year, dto.EndTime.Month, DateTime.DaysInMonth(dto.EndTime.Year, dto.EndTime.Month), 23, 59, 59, dto.EndTime.Kind);
            dto.ExportSalesAcct = await UnitWork.Find<FinanceLedgeracct>(x => x.IsExportSalesAcct ).Select(x => x.AccountCode).ToListAsync();
            //汇兑损益
            dto.ExchangeGainsAndLossesAcct = new List<string>() { "660302" };
            var monthCount = GetMonthsCount(dto.BeginTime, dto.EndTime);
            var idcTableData = await httpClientHelper.Post<TableData<List<GetSalePersonReturnMoneyDto>>>(configuration.GetValue<string>(Define.IDCApi) + "/api/SalesInvoice/GetSalePersonReturnMoney", dto, idcToken);
            var userMapQry = from u in UnitWork.Find<User>(null) join m in UnitWork.Find<NsapUserMap>(null) on u.Id equals m.UserID where u.Status == 0 select new { User = u, m.NsapUserId };
            var nsapUserMap = await userMapQry.ToListAsync();
            var slpInErp3Qry = from s in UnitWork.Find<sbo_user>(null) join e in UnitWork.Find<base_user>(null) on s.user_id equals e.user_id select new { s.sale_id, e.user_id, e.user_nm };
            var slpInErp3Dictionary = await slpInErp3Qry.ToListAsync();
            var userNameOrgDictionary = userDepartMsgHelp.GetUserNameOrgDictionary();
            var salerefundAmounts = await UnitWork.Find<SaleRefund>(x => x.DocDate >= dto.BeginTime && x.DocDate <= dto.EndTime && x.Status == SaleRefundStatus.Pass).Select(x => new
            {
                x.Creator,
                x.ApplyAmount,
                x.ApplyCurrency,
                x.DocDate
            }).ToListAsync();
            var refunds = new List<GetSalePersonReturnMoneyDto>();
            foreach (var salerefund in salerefundAmounts)
            {
                var refund = new GetSalePersonReturnMoneyDto();
                refund.SaleCode = slpInErp3Dictionary.FirstOrDefault(x => x.user_id == (uint)(nsapUserMap.FirstOrDefault(x => x.User.Id == salerefund.Creator)?.NsapUserId ?? 0))?.sale_id??0 ;
                refund.SalePerson =nsapUserMap.FirstOrDefault(x => x.User.Id == salerefund.Creator)?.User.Name;
                refund.ReturnDate = salerefund.DocDate.Value.Date;
                var returnMoneyRmb = - salerefund.ApplyAmount;
                refund.ReturnMoney = returnMoneyRmb;
                refund.DoReturnAmount = salerefund.ApplyCurrency == "RMB" ? returnMoneyRmb : 0;
                refund.ExReturnAmount = salerefund.ApplyCurrency != "RMB" ? returnMoneyRmb : 0;
                refunds.Add(refund);
            }

            if (idcTableData.Code == 200)
            {
                var saleBounds = new List<SalePerformance>();
                var groupData = idcTableData.Data.Concat(refunds).Select(x => new  { x.SaleCode, x.SalePerson, x.ReturnMoney, x.DoReturnAmount, x.ExReturnAmount }).GroupBy(x => new { x.SaleCode, x.SalePerson }).Select(x => new { x.Key.SaleCode, x.Key.SalePerson, ReturnMoney = x.Sum(y => y.ReturnMoney), DomesticSalesAmount = x.Sum(y=>y.DoReturnAmount), ExportSalesAmount = x.Sum(y => y.ExReturnAmount) });
                foreach (var data in groupData)
                {
                    var erp3userId = slpInErp3Dictionary.FirstOrDefault(x => x.sale_id == data.SaleCode && x.user_nm == data.SalePerson)?.user_id;
                    if (erp3userId != null)
                    {
                        var user = nsapUserMap.FirstOrDefault(x => x.NsapUserId == erp3userId)?.User;
                        if (user != null)
                        {
                            var deptName = userNameOrgDictionary.TryGetValue(user.Name, out var org) ? org : string.Empty;
                            var (totalMonthsOfWork, workYear, workYearLong) = CaculateWorkYear(user.EntryTime.Value);
                            var entryYear = GetEntryYearLevel(user.EntryTime.Value);    
                            var saleBound = new SalePerformance()
                            {
                                Department = deptName,
                                UserName = !string.IsNullOrWhiteSpace(deptName) ? (deptName + "-" + user.Name) : user.Name,
                                EntryDate = user.EntryTime.Value,
                                WorkMonth = totalMonthsOfWork,
                                WorkYear = workYear,
                                WorkYearLong = workYearLong,
                                EntryYear = EnumUtility.GetDescription(entryYear),
                                EntryYearEnum = entryYear,
                                ReturnMoney = data.ReturnMoney,
                                AverageMonthlyReturnMoney = decimal.Round(data.ReturnMoney / monthCount, 2, MidpointRounding.AwayFromZero),
                                AverageYearlyReturnMoney = decimal.Round(data.ReturnMoney / (decimal)workYearLong, 2, MidpointRounding.AwayFromZero),
                                DomesticSalesAmount = data.DomesticSalesAmount,
                                ExportSalesAmount = data.ExportSalesAmount,
                                AverageWorkMonthlyReturnMoney = decimal.Round(data.ReturnMoney / totalMonthsOfWork,2, MidpointRounding.AwayFromZero) 
                            };
                            saleBounds.Add(saleBound);
                        }
                    }
                }
                saleBounds = saleBounds.OrderByDescending(x => x.ReturnMoney).ToList();
                var saleBoundsByEntryYear = saleBounds.GroupBy(x => x.EntryYearEnum).Select(x => new SalePerformanceByEntryYear
                {
                    EntryYear = EnumUtility.GetDescription(x.Key),
                    EntryYearEnum = x.Key,
                    ReturnMoney = decimal.Round(x.Sum(x => x.ReturnMoney), 2, MidpointRounding.AwayFromZero),
                    EntryYearNumbers = x.Count(),
                    AverageMonthlyReturnMoney = decimal.Round(x.Sum(x => x.ReturnMoney) / monthCount, 2, MidpointRounding.AwayFromZero),
                    TotalAverageMonthlyReturnMoneyPerPerson = decimal.Round(x.Sum(x => x.ReturnMoney) / x.Count() / x.Sum(x => x.WorkMonth), 2, MidpointRounding.AwayFromZero),
                    TotalAverageYearlyReturnMoneyPerPerson = decimal.Round(x.Sum(x => x.ReturnMoney) / x.Count() / (decimal)x.Sum(x => x.WorkYearLong), 2, MidpointRounding.AwayFromZero),   
                    WorkYear = x.Sum(x => x.WorkYear),
                    WorkYearLong = Math.Round(x.Sum(x => x.WorkYearLong), 1, MidpointRounding.AwayFromZero),
                    AverageWorkMonthlyReturnMoney = Math.Round( x.Sum(y=>y.ReturnMoney) / x.Sum(y=> y.WorkMonth) ,2 , MidpointRounding.AwayFromZero),
                    children = x.OrderByDescending(x => x.ReturnMoney).ToList(),
                }).OrderBy(x => x.EntryYearEnum);
                var saleBoundsByDepartment = saleBounds.GroupBy(x => x.Department).Select(x => new SalePerformanceByDepartment
                {
                    Department = x.Key,
                    DepartmentsMemberNumber = x.Count(),
                    ReturnMoney = decimal.Round(x.Sum(x => x.ReturnMoney), 2, MidpointRounding.AwayFromZero),
                    AverageMonthlyReturnMoney = decimal.Round(x.Sum(x => x.ReturnMoney) / monthCount, 2, MidpointRounding.AwayFromZero),
                    //AverageYearlyReturnMoney = decimal.Round(x.Sum(x => x.ReturnMoney) / (decimal)Math.Round(x.Sum(x => x.WorkYearLong), 1, MidpointRounding.AwayFromZero), 2, MidpointRounding.AwayFromZero),
                    AverageWorkMonthlyReturnMoney = Math.Round(x.Sum(y => y.ReturnMoney) / x.Sum(y => y.WorkMonth), 2 , MidpointRounding.AwayFromZero),
                    BestReturnPercent = string.Format("{0}%",decimal.Round(x.Max(x => x.ReturnMoney) / x.Sum(x => x.ReturnMoney) * 100 , 2, MidpointRounding.AwayFromZero)),
                    children = x.OrderByDescending(x => x.ReturnMoney).ToList(),
                });

                result.saleBounds = saleBounds;
                result.saleBoundsByWorkYear = saleBoundsByEntryYear.ToList();
                result.saleBoundsByDepartment = saleBoundsByDepartment.ToList();
                return result;
            }
            return result;
        }

        /// <summary>
        /// 处理入职年限相关数据
        /// </summary>
        /// <param name="entryTime"></param>
        /// <returns></returns>
        private static (decimal totalMonthsOfWork, int workYear, double workYearLong) CaculateWorkYear(DateTime entryTime)
        {
            var currentDate = DateTime.Now;
            int workYear;
            if (currentDate.Month < entryTime.Month)
            {
                workYear = currentDate.Year - entryTime.Year - 1;
            }
            else
            {
                workYear = currentDate.Year - entryTime.Year;
            }

            decimal totalMonthsOfWork = (currentDate.Year - entryTime.Year) * 12 + currentDate.Month - entryTime.Month;
            // 如果当前日期的天数小于入职日期的天数，那么还没有满一个月，则按照入职当月15号之前算一个月，15号之后算半个月
            if (currentDate.Day < entryTime.Day)
            {
                if (entryTime.Day <= 15)
                {
                    totalMonthsOfWork += 1.0M;
                }
                else
                {
                    totalMonthsOfWork += 0.5M;
                }
            }

            var workYearLong = CommonHelper.GetMonth(entryTime);
            return (totalMonthsOfWork, workYear, workYearLong);
        }

        /// <summary>
        /// 获取服务提成节点直通率看板数据
        /// </summary>
        /// <param name="dto"></param>
        /// <returns></returns>
        private async Task<List<FinanceCardDataDetailDto>> GetOutSourcCard (FinanceCardRequestDto dto)
        {
            var cardDataDtoList = new List<FinanceCardDataDetailDto>(4);
            var outSourcsFlowInstanIds = await UnitWork.Find<Outsourc>(x => x.FlowInstanceId != null && x.FlowInstanceId != "").Select(x => x.FlowInstanceId).ToListAsync();

            var allHistorys = await (from a in UnitWork.Find<FlowInstance>(null)
                                     join b in UnitWork.Find<FlowInstanceOperationHistory>(null) on a.Id equals b.InstanceId
                                     where
               outSourcsFlowInstanIds.Contains(a.Id)
                                     select new { InstanceId = b.InstanceId, Content = b.Content, CreateDate = b.CreateDate, IntervalTime = b.IntervalTime, b.ApprovalResult, CurrentActivityName = a.ActivityName }).ToListAsync();



            // --- 刚好到财务初审、财务复审、总经理审批、出纳 这些个环节的审批流的数量 --- START
            // 到达某个节点只会在主表有节点名称字段，到达时间要查前一个节点的创建时间
            // 前一个节点的时间 便是 CreatDate
            var historysJustToEnumrable = allHistorys.Where(x => x.CreateDate >= dto.StartTime && x.CreateDate <= dto.EndTime);

            var justFirstAuditCount = historysJustToEnumrable.Where(x => x.CurrentActivityName == "财务审核").GroupBy(x => x.InstanceId).Count();

            var justManagerAuditCount = historysJustToEnumrable.Where(x => x.CurrentActivityName == managerAudit).GroupBy(x => x.InstanceId).Count();

            var justPayAuditCount = historysJustToEnumrable.Where(x => x.CurrentActivityName == "财务支付").GroupBy(x => x.InstanceId).Count();
            //  ---刚好到财务初审、财务复审、总经理审批、出纳 这些个环节的审批流的数量 --- END

            //已审过的，其到达这个节点的时间 应该是 CreateDate - TimeSpan.FromSeconds(IntervalTime) 即 前一个节点的创建时间
            var historysAlreadyEnumrable = allHistorys.Where(x => (x.CreateDate - TimeSpan.FromSeconds(x.IntervalTime.HasValue ? (double)x.IntervalTime.Value + 1 : 0)) >= dto.StartTime && (x.CreateDate - TimeSpan.FromSeconds(x.IntervalTime.HasValue ? (double)x.IntervalTime.Value + 1 : 0)) <= dto.EndTime);
            var alreadyFirstAudits = historysAlreadyEnumrable.Where(x => x.Content == "财务审核").ToList();
            var alreadyManagerAudits = historysAlreadyEnumrable.Where(x => x.Content == managerAudit).ToList();
            var alreadyPayAudits = historysAlreadyEnumrable.Where(x => x.Content == "财务支付").ToList();
            var targetSecond = dto.TargetHour * 3600;
            //计算到过时间范围内到过 财务初审阶段 并且 在目标时间内审核完成的 单据数量

            var alreadyFirstTargetCount = alreadyFirstAudits.Count(x => (x.IntervalTime.HasValue ? x.IntervalTime.Value : 0) <= targetSecond);
            var alreadyManagerTargetCount = alreadyManagerAudits.Count(x => (x.IntervalTime.HasValue ? x.IntervalTime.Value : 0) <= targetSecond);
            var alreadyPayTargetCount = alreadyPayAudits.Count(x => (x.IntervalTime.HasValue ? x.IntervalTime.Value : 0) <= targetSecond);

       



            //财务初审
            var firstAuditCardDataDetailDto = new FinanceCardDataDetailDto() { StageName = "财务初审", TargetDuration = dto.TargetHour, TargetCount = alreadyFirstTargetCount, TotalCount = alreadyFirstAudits.Count + justFirstAuditCount, AchievingRate = (alreadyFirstAudits.Count + justFirstAuditCount) == 0 ? 0 : Math.Round(alreadyFirstTargetCount * 0.1D / (alreadyFirstAudits.Count + justFirstAuditCount) * 1000, 2) };
            cardDataDtoList.Add(firstAuditCardDataDetailDto);

            //财务复审
            var secondAuditCardDataDetailDto = new FinanceCardDataDetailDto() { StageName = "财务复审", TargetDuration = dto.TargetHour, TargetCount = 0, TotalCount = 0, AchievingRate = 0 };
            cardDataDtoList.Add(secondAuditCardDataDetailDto);

            //总经理审批
            var managerAuditCardDataDetailDto = new FinanceCardDataDetailDto() { StageName = managerAudit, TargetDuration = dto.TargetHour, TargetCount = alreadyManagerTargetCount, TotalCount = alreadyManagerAudits.Count + justManagerAuditCount, AchievingRate = (alreadyManagerAudits.Count + justManagerAuditCount) == 0 ? 0 : Math.Round(alreadyManagerTargetCount * 0.1D / (alreadyManagerAudits.Count + justManagerAuditCount) * 1000, 2) };
            cardDataDtoList.Add(managerAuditCardDataDetailDto);

            //出纳
            var reciveAndPayAuditCardDataDetailDto = new FinanceCardDataDetailDto() { StageName = "出纳", TargetDuration = dto.TargetHour, TargetCount = alreadyPayTargetCount, TotalCount = alreadyPayAudits.Count + justPayAuditCount, AchievingRate = (alreadyPayAudits.Count + justPayAuditCount) == 0 ? 0 : Math.Round(alreadyPayTargetCount * 0.1D / (alreadyPayAudits.Count + justPayAuditCount) * 1000, 2) };
            cardDataDtoList.Add(reciveAndPayAuditCardDataDetailDto);

            return cardDataDtoList;
        }
        /// <summary>
        /// 获取报销单节点直通率看板数据
        /// </summary>
        /// <param name="dto"></param>
        /// <returns></returns>
        private async Task<List<FinanceCardDataDetailDto>> GetReimburseCard(FinanceCardRequestDto dto)
        {
            var cardDataDtoList = new List<FinanceCardDataDetailDto>(4);
            var flowInstanceIds = await UnitWork.Find<ReimburseInfo>(x => x.FlowInstanceId != null && x.FlowInstanceId != "").Select(x=>x.FlowInstanceId).ToListAsync();
            var allHistorys = await  (from a in UnitWork.Find< FlowInstance >(null) join b in UnitWork.Find<FlowInstanceOperationHistory>(null) on a.Id equals b.InstanceId where 
                flowInstanceIds.Contains(a.Id) select  new  { InstanceId = b.InstanceId, Content = b.Content, CreateDate = b.CreateDate, IntervalTime =b.IntervalTime ,b.ApprovalResult,CurrentActivityName  =a.ActivityName }).ToListAsync();



            // --- 刚好到财务初审、财务复审、总经理审批、出纳 这些个环节的审批流的数量 --- START
            // 到达某个节点只会在主表有节点名称字段，到达时间要查前一个节点的创建时间
            // 前一个节点的时间 便是 CreatDate
            var historysJustToEnumrable = allHistorys.Where(x => x.CreateDate  >= dto.StartTime && x.CreateDate <= dto.EndTime);

            var justFirstAuditCount = historysJustToEnumrable.Where(x => x.CurrentActivityName == "财务初审").GroupBy(x => x.InstanceId).Count(); 
         
            var justSecondAuditCount = historysJustToEnumrable.Where(x => x.CurrentActivityName == "财务复审").GroupBy(x => x.InstanceId).Count();
            
            var justManagerAuditCount = historysJustToEnumrable.Where(x => x.CurrentActivityName == managerAudit).GroupBy(x => x.InstanceId).Count();

            var justPayAuditCount = historysJustToEnumrable.Where(x => x.CurrentActivityName == "出纳").GroupBy(x => x.InstanceId).Count();
            //  ---刚好到财务初审、财务复审、总经理审批、出纳 这些个环节的审批流的数量 --- END

            //已审过的，其到达这个节点的时间 应该是 CreateDate - TimeSpan.FromSeconds(IntervalTime) 即 前一个节点的创建时间
            var historysAlreadyEnumrable = allHistorys.Where(x => (x.CreateDate - TimeSpan.FromSeconds(x.IntervalTime.HasValue ? (double)x.IntervalTime.Value + 1 : 0)) >= dto.StartTime && (x.CreateDate - TimeSpan.FromSeconds(x.IntervalTime.HasValue ? (double)x.IntervalTime.Value + 1 : 0)) <= dto.EndTime);
            var alreadyFirstAudits = historysAlreadyEnumrable.Where(x => x.Content == "财务初审" ).ToList();
            var alreadySecondAudits = historysAlreadyEnumrable.Where(x => x.Content == "财务复审").ToList();
            var alreadyManagerAudits = historysAlreadyEnumrable.Where(x => x.Content == managerAudit).ToList();
            var alreadyPayAudits = historysAlreadyEnumrable.Where(x => x.Content == "出纳").ToList();
            var targetSecond = dto.TargetHour * 3600;
            //计算到过时间范围内到过 财务初审阶段 并且 在目标时间内审核完成的 单据数量
            var alreadyFirstTargetCount = alreadyFirstAudits.Count(x => (x.IntervalTime.HasValue ? x.IntervalTime.Value : 0) <= targetSecond);
            var alreadySecondTargetCount = alreadySecondAudits.Count(x => (x.IntervalTime.HasValue ? x.IntervalTime.Value : 0) <= targetSecond);
            var alreadyManagerTargetCount = alreadyManagerAudits.Count(x => (x.IntervalTime.HasValue ? x.IntervalTime.Value : 0) <= targetSecond);
            var alreadyPayTargetCount = alreadyPayAudits.Count(x => (x.IntervalTime.HasValue ? x.IntervalTime.Value : 0) <= targetSecond);

           




            //财务初审
            var firstAuditCardDataDetailDto = new FinanceCardDataDetailDto() { StageName = "财务初审", TargetDuration = dto.TargetHour, TargetCount = alreadyFirstTargetCount, TotalCount = alreadyFirstAudits.Count + justFirstAuditCount, AchievingRate = (alreadyFirstAudits.Count + justFirstAuditCount) ==0 ? 0 : Math.Round( alreadyFirstTargetCount * 0.1D / (alreadyFirstAudits.Count + justFirstAuditCount) * 1000 ,2) };
            cardDataDtoList.Add(firstAuditCardDataDetailDto);
            
            //财务复审
            var secondAuditCardDataDetailDto = new FinanceCardDataDetailDto() { StageName = "财务复审", TargetDuration = dto.TargetHour, TargetCount = alreadySecondTargetCount, TotalCount = alreadySecondAudits.Count + justSecondAuditCount, AchievingRate =(alreadySecondAudits.Count + justSecondAuditCount)==0 ? 0 :  Math.Round(alreadySecondTargetCount * 0.1D / (alreadySecondAudits.Count + justSecondAuditCount) * 1000, 2) };
            cardDataDtoList.Add(secondAuditCardDataDetailDto);

            //总经理审批
            var managerAuditCardDataDetailDto = new FinanceCardDataDetailDto() { StageName = managerAudit, TargetDuration = dto.TargetHour, TargetCount = alreadyManagerTargetCount, TotalCount = alreadyManagerAudits.Count + justManagerAuditCount, AchievingRate =(alreadyManagerAudits.Count + justManagerAuditCount) ==0?0: Math.Round(alreadyManagerTargetCount * 0.1D / (alreadyManagerAudits.Count + justManagerAuditCount) * 1000, 2) };
            cardDataDtoList.Add(managerAuditCardDataDetailDto);

            //出纳
            var reciveAndPayAuditCardDataDetailDto = new FinanceCardDataDetailDto() { StageName = "出纳", TargetDuration = dto.TargetHour, TargetCount = alreadyPayTargetCount, TotalCount = alreadyPayAudits.Count + justPayAuditCount, AchievingRate = (alreadyPayAudits.Count + justPayAuditCount)== 0? 0: Math.Round(alreadyPayTargetCount * 0.1D / (alreadyPayAudits.Count + justPayAuditCount) * 1000, 2 ) };
            cardDataDtoList.Add(reciveAndPayAuditCardDataDetailDto);

            return cardDataDtoList;
        }
        /// <summary>
        /// 获取采购付款节点直通率看板数据
        /// </summary>
        /// <param name="dto"></param>
        /// <returns></returns>
        private async Task<List<FinanceCardDataDetailDto>> GetWmsPayPurchaseCard(FinanceCardRequestDto dto)
        {
            var cardDataDtoList = new List<FinanceCardDataDetailDto>(4);
            var wfSteps = GetWfSteps();

            var firstAuditStep = wfSteps.FirstOrDefault(x => x.Name == "提交"); // 财务初审
            var secondAuditStep = wfSteps.FirstOrDefault(x => x.Name == "财务对账"); // 财务复审
            var managerAuditStep = wfSteps.FirstOrDefault(x => x.Name == managerAudit);
            var receiveAndPayAuditStep = wfSteps.FirstOrDefault(x => x.Name == "财务应用"); //出纳

            var payPurchaseTask = UnitWork.Find<WfTask>(null).Where(x=>x.WfId ==16).Select(x=>new { x.TaskId,x.CurrentStepId });
            var taskIds = await payPurchaseTask.Select(x=>x.TaskId).ToListAsync();
            var wfTaskSteps = UnitWork.Find<WfTaskStep>(x => taskIds.Contains(x.TaskId));
            //wms 创建审批流到达哪个节点就会创建那个节点的 wfTaskStep,
            //要区分是不是当前节点 就得加个CurrentStepId
            var wfTaskStepsWithTask =  await (from p in payPurchaseTask join s in wfTaskSteps on p.TaskId equals s.TaskId 
                                       where s.CreateTime >= dto.StartTime && s.CreateTime <= dto.EndTime
                                       select new
                                       {
                                           p.TaskId,
                                           p.CurrentStepId,
                                           s.StepId,
                                           s.Status,
                                           s.StatusTime,
                                           s.CreateTime
                                       }).ToListAsync();

            // 刚好到达 财务初审 节点的审批流 的数量
            var justToFirstAudtiTasksCount = wfTaskStepsWithTask.Count(x => firstAuditStep.StepId == x.CurrentStepId && x.Status == (int)WfTaskStepStatus.WaitAudit  );
            // 刚好到达 财务复审 节点的审批流 的数量
            var justToSecondAuditTasksCount = wfTaskStepsWithTask.Count(x => secondAuditStep.StepId == x.CurrentStepId && x.Status == (int)WfTaskStepStatus.WaitAudit );
            // 刚好到达 财务复审 节点的审批流 的数量
            var justToManagerAuditTasksCount = wfTaskStepsWithTask.Count(x => managerAuditStep.StepId == x.CurrentStepId && x.Status == (int)WfTaskStepStatus.WaitAudit );
            // 刚好到达 财务复审 节点的审批流 的数量
            var justToReceiveAndPayAuditTasksCount = wfTaskStepsWithTask.Count(x => receiveAndPayAuditStep.StepId == x.CurrentStepId && x.Status == (int)WfTaskStepStatus.WaitAudit );
            
            var alreadyFirstAuditTasks = wfTaskStepsWithTask.Where(x => (firstAuditStep.StepId != x.CurrentStepId && x.StepId == firstAuditStep.StepId ) 
                || (firstAuditStep.StepId == x.CurrentStepId && x.Status == (int)WfTaskStepStatus.Reject )).ToList();
            var alreadySecondAuditTasks = wfTaskStepsWithTask.Where(x=> (secondAuditStep.StepId != x.CurrentStepId && x.StepId == secondAuditStep.StepId)
                || (secondAuditStep.StepId == x.CurrentStepId && x.Status == (int)WfTaskStepStatus.Reject)).ToList();
            var alreadyManagerAuditTasks = wfTaskStepsWithTask.Where(x => (secondAuditStep.StepId != x.CurrentStepId && x.StepId == secondAuditStep.StepId)
                || (secondAuditStep.StepId == x.CurrentStepId && x.Status == (int)WfTaskStepStatus.Reject)).ToList();
            var alreadyReceiveAndPayAuditTasks = wfTaskStepsWithTask.Where(x => (secondAuditStep.StepId != x.CurrentStepId && x.StepId == secondAuditStep.StepId)
                || (secondAuditStep.StepId == x.CurrentStepId && x.Status == (int)WfTaskStepStatus.Reject)).ToList();

            var targetSecond = dto.TargetHour * 3600;

            var firstAuditAchiveCount = alreadyFirstAuditTasks.Count(x => (x.StatusTime.Value - x.CreateTime).Seconds <= targetSecond);
            var secondAuditAchiveCount =  alreadySecondAuditTasks.Count(x => (x.StatusTime.Value - x.CreateTime).Seconds <= targetSecond);
            var managerAuditAchiveCount = alreadyManagerAuditTasks.Count(x => (x.StatusTime.Value - x.CreateTime).Seconds <= targetSecond);
            var receiveAndPayAchiveCount = alreadyReceiveAndPayAuditTasks.Count(x => (x.StatusTime.Value - x.CreateTime).Seconds <= targetSecond);  

            //财务初审
            var firstAuditCardDataDto = new FinanceCardDataDetailDto() { StageName = "财务初审", TargetDuration = dto.TargetHour, TotalCount = justToFirstAudtiTasksCount + alreadyFirstAuditTasks.Count, TargetCount = firstAuditAchiveCount, AchievingRate = (justToFirstAudtiTasksCount + alreadyFirstAuditTasks.Count) == 0 ? 0: Math.Round( firstAuditAchiveCount * 0.1D / (justToFirstAudtiTasksCount + alreadyFirstAuditTasks.Count) * 1000,2 )};
            cardDataDtoList.Add(firstAuditCardDataDto);

            //财务复审
            var secondAuditTotalCount = new FinanceCardDataDetailDto() { StageName = "财务复审", TargetDuration = dto.TargetHour,  TotalCount = justToSecondAuditTasksCount + alreadySecondAuditTasks.Count, TargetCount = secondAuditAchiveCount, AchievingRate =(justToSecondAuditTasksCount + alreadySecondAuditTasks.Count) == 0 ? 0 : Math.Round(secondAuditAchiveCount * 0.1D / (justToSecondAuditTasksCount + alreadySecondAuditTasks.Count) * 1000  ,2)} ;
            cardDataDtoList.Add(secondAuditTotalCount);

            //总经理审批
            var managerAuditTotalCount = new FinanceCardDataDetailDto() { StageName = managerAudit, TargetDuration = dto.TargetHour,  TotalCount = justToManagerAuditTasksCount + alreadyManagerAuditTasks.Count, TargetCount = managerAuditAchiveCount, AchievingRate = (justToManagerAuditTasksCount + alreadyManagerAuditTasks.Count)== 0 ? 0 : Math.Round(managerAuditAchiveCount * 0.1D / (justToManagerAuditTasksCount + alreadyManagerAuditTasks.Count) * 1000 ,2)} ;
            cardDataDtoList.Add(managerAuditTotalCount);

            //出纳
            var reciveAndPayAuditTotalCount = new FinanceCardDataDetailDto() { StageName = "出纳", TargetDuration = dto.TargetHour,  TotalCount = justToReceiveAndPayAuditTasksCount + alreadyReceiveAndPayAuditTasks.Count, TargetCount = receiveAndPayAchiveCount, AchievingRate = (justToReceiveAndPayAuditTasksCount + alreadyReceiveAndPayAuditTasks.Count) == 0 ? 0 : Math.Round(receiveAndPayAchiveCount * 0.1D / (justToReceiveAndPayAuditTasksCount + alreadyReceiveAndPayAuditTasks.Count) * 1000, 2) } ;
            cardDataDtoList.Add(reciveAndPayAuditTotalCount);

            return cardDataDtoList;
        }
        /// <summary>
        /// wms 审批流流程状态
        /// </summary>
        public enum WfTaskStepStatus
        {
            /// <summary>
            /// 撤回
            /// </summary>
            Recall = 6,

            /// <summary>
            /// 待审核
            /// </summary>
            WaitAudit = 1,

            /// <summary>
            /// 通过
            /// </summary>
            Pass = 9,

            /// <summary>
            /// 驳回
            /// </summary>
            Reject = 10
        }
        /// <summary>
        /// 获取年限等级Enum
        /// </summary>
        /// <param name="entryTime"></param>
        /// <returns></returns>
        public static EntryYear GetEntryYearLevel(DateTime entryTime)
        {
            var workYear = 0;
            var currentDate = DateTime.Now;
            if (currentDate.Month < entryTime.Month)
            {
                workYear = currentDate.Year - entryTime.Year - 1;
            }
            else
            {
                workYear = currentDate.Year - entryTime.Year;
            }

            if (workYear >= 10)
            {
                return EntryYear.OverTenYears;
            }
            else if (workYear >= 5 && workYear < 10)
            {
                return EntryYear.FiveToTenYears;
            }
            else if (workYear >= 3 && workYear < 5)
            {
                return EntryYear.ThreeToFiveYears;
            }
            else if (workYear >= 1 && workYear < 3)
            {
                return EntryYear.OneToThreeYears;
            }
            else
            {
                return EntryYear.LessThanOneYear;
            }
          
        }

        /// <summary>
        /// 获取销售业绩看板权限
        /// </summary>
        /// <returns></returns>
        public GetSaleBoundsAuthority GetSaleBoundsAuthority()
        {
            var authority = new GetSaleBoundsAuthority();
            authority.ReportCheckAuthority = false;
            var logincontext = _auth.GetCurrentUser();
            var roles = logincontext.Roles;
            if (roles.Exists(x => x.RoleKey == RoleKeyConsts.FICO_HOME_SALEBOUND_DEPT_PERSON) || roles.Exists(x => x.RoleKey == RoleKeyConsts.FICO_HOME_SALEBOUND_ALLDATA))
            {
                authority.ChangeArgAuthority = true;
            }
            if(roles.Exists(x=>x.RoleKey == RoleKeyConsts.FICO_HOME_SALEBOUND_REPORT))
            {
                authority.ReportCheckAuthority = true;
            }
            return authority;
        }
        /// <summary>
        /// 销售业绩看板销售员下拉
        /// </summary>
        /// <returns></returns>
        private async Task<List<SalePersonDropDownOption>> SalePersonDropDownOptions()
        {
            var persons = await orderCommonApp.GetSboUserByCode(isConcatDept: true);
            var slpCodeToBiUserId = await orderCommonApp.GetSboUserWithBIUser();
            var finalPersons = new List<SalePersonDropDownOption>();
            
            var finalPersonsGroup1 = (from p in persons
                                        join s in slpCodeToBiUserId
                    on p.Id.ToString() equals s.Id.ToString()
                    into pss from ps in pss.DefaultIfEmpty()
                                        where (ps?.Name.ToString()??string.Empty) != string.Empty
                                        select new
                                        {
                                            p.Id,
                                            p.Name,
                                            Userid = ps.Name,
                                        }
                            into t
                                        group t by t.Id into g
                                        select g).ToList();

            foreach (var g in finalPersonsGroup1)
            {
                var option = new SalePersonDropDownOption
                {
                    Id = g.Key,
                    Name = g.FirstOrDefault()?.Name.ToString(),
                    UserIds = g.Select(x => x.Userid.ToString()).ToList()
                };
                finalPersons.Add(option);
            }
            return finalPersons;
        }
        /// <summary>
        /// 销售业绩看板下拉
        /// </summary>
        /// <returns></returns>
        public async Task<GetSaleBoundsDropDownOptions> GetSaleBoundsDropDownOptions()
        {
            var options = new GetSaleBoundsDropDownOptions(); 
            var logincontext = _auth.GetCurrentUser();
            var roles = logincontext.Roles;

            var finalPersons = await SalePersonDropDownOptions();
            if (roles.Exists(x => x.RoleKey == RoleKeyConsts.FICO_HOME_SALEBOUND_ALLDATA))
            {
                options.Persons = finalPersons;
                var depts = options.Persons.Select(x =>
                {
                    var detpSlpNameSplit = x.Name.ToString().Split('-');
                    var deptName = string.Empty;
                    if (detpSlpNameSplit.Length > 1)
                    {
                        deptName = detpSlpNameSplit[0];
                    }
                    return new DropDownOption{ Id = 
                        deptName,Name = deptName };
                });
                options.Depts = depts.Where(x=>x.Id.ToString() != string.Empty).Distinct(new DropDownOptionComparer()).ToList();
            }
            else if(!roles.Exists(x => x.RoleKey == RoleKeyConsts.FICO_HOME_SALEBOUND_ALLDATA)
                && roles.Exists(x => x.RoleKey == RoleKeyConsts.FICO_HOME_SALEBOUND_DEPT_PERSON))
            {
                var currentDept =  logincontext.Orgs.FirstOrDefault()?.Name;
                options.Persons = finalPersons.Where(x => x.Name.ToString().Contains(currentDept)).ToList();
                options.Depts = new List<DropDownOption>();
                options.Depts.Add(new DropDownOption { Id = currentDept, Name = currentDept });
            }
            else
            {
                options.Persons = new List<SalePersonDropDownOption>();
                options.Depts = new List<DropDownOption>();
            }
            return options;
        }


        /// <summary>
        /// 付款总额= 当月总经理已审批过但未付出去的+ 当月出纳已付出去的,若非当月则为月份里面付出去的金额
        /// 销售额=当月累计交货金额-当月累计退货金额.
        /// 回款额=当月累计收款金额-当月累计退款金额.
        /// 税额= 当月税额
        /// </summary>
        /// <param name="req"></param>
        /// <returns></returns>
        public async Task<(Response.GetSaleBoundsLineChart thisYear, Response.GetSaleBoundsLineChart lastYear)> GetSaleBoundsLineChart( GetSaleBoundsLineChartReq req)
        {
            var idcToken = await httpClientHelper.GetIDCToken();
            
            var logincontext = _auth.GetCurrentUser();

            //处理传入时间参数
            var monthsData = GetMonthsBetweenAndLastYearMonths(req.BeginTime, req.EndTime);
            var months = monthsData.OriginalMonths.Union(monthsData.Minus12Months).Select(x => x.ToString(Define.YearMonth));
            (req.BeginTime, req.EndTime) = GetMonthFirstDayAndFinalDayMinus12Months(req.BeginTime, req.EndTime);

            
            var loginCompanys = await UnitWork.Find<Category>(c => c.TypeId.Equals(Define.CompanyEntity)).Select(c => new { c.Name, c.DtCode,c.Extension }).ToListAsync();
            var loginCompanyInfos = loginCompanys.Select(x => 
            new { Code = x.DtCode, Extension = JsonConvert.DeserializeObject<CompanyEntityExtension>(x.Extension) }).ToList();
            //将Indicator 转换为 AcctCode 作为查询条件
            var acctCodes = await UnitWork.Find<FinanceLedgeracct>(null).WhereIf(!string.IsNullOrEmpty(req.Indicator), x => x.Indicator == req.Indicator ).Select(x =>new { x.AccountCode,x.Indicator }).ToListAsync();

            var crmOidcs = await UnitWork.Find<crm_oidc>(null).ToListAsync();
            //假设已设置区分好销售的标识、采购的标识
            var saleOidcs = crmOidcs.Where(x => loginCompanyInfos.Any(l => l.Code == x.Code && (l.Extension?.Se ?? false))).ToList();
            var buyOidcs = crmOidcs.Where(x => loginCompanyInfos.Any(l => l.Code == x.Code && (l.Extension?.Po ?? false))).ToList();

            var (haveRight,slpCodes,userIds ) =await GetSaleBoundsLineChartHanldeReq(req, logincontext);
            if (!haveRight)
            {
                return (new Response.GetSaleBoundsLineChart
                {
                    ReturnMoneyData = new List<GetSaleBoundsLineChartItem>(),
                    SaleMoneyData = new List<GetSaleBoundsLineChartItem>(),
                    PaymentDate = new List<GetPaymentLineChartItem>(),
                    PoIndicatorSorts = new List<IndicatorSort>(),
                    SeIndicatorSorts = new List<IndicatorSort>(),
                    TaxMoneyData = new List<GetSaleBoundsLineChartItem>(),
                }
                , new Response.GetSaleBoundsLineChart
                {
                    ReturnMoneyData = new List<GetSaleBoundsLineChartItem>(),
                    SaleMoneyData = new List<GetSaleBoundsLineChartItem>(),
                    PaymentDate = new List<GetPaymentLineChartItem>(),
                    PoIndicatorSorts = new List<IndicatorSort>(),
                    SeIndicatorSorts = new List<IndicatorSort>(),
                    TaxMoneyData = new List<GetSaleBoundsLineChartItem>(),
                });
            }
            var GetSaleAmountAndReturnMoneyReq = new GetSaleAmountAndReturnMoneyReq
            {
                SlpCodes = slpCodes.Any() ? slpCodes : null,
                BeginTime = req.BeginTime,
                EndTime = req.EndTime,
                Indicator = req.Indicator,
                AcctCodes = !string.IsNullOrEmpty(req.Indicator) ? acctCodes.Select(x=>x.AccountCode).ToList() : new List<string>(),
            };

            //查询退款的数据
            var refundMoneyResp =await UnitWork.Find<SaleRefund>(x => x.DocDate >= req.BeginTime && x.DocDate <= req.EndTime && x.Status == SaleRefundStatus.Pass )
                .WhereIf(userIds.Count >0 , x=> userIds.Contains(x.Creator))
                .WhereIf(!string.IsNullOrEmpty(req.Indicator),x=> x.Indicator == req.Indicator)
                .Select(
                x => new RefundMoneyAndDate
                {
                    Date = x.DocDate.Value.Date,
                    RefundMoney = x.ApplyAmount,
                    Indicator = x.Indicator
                }).ToListAsync() ;


            var getSaleBoundsLineChart = new Response.GetSaleBoundsLineChart();
            getSaleBoundsLineChart.SeIndicatorSorts = loginCompanyInfos.Where(x => (x.Extension?.Se ?? false)).OrderBy(x => x.Extension?.SeSortNo ?? 99).Select(x => new IndicatorSort { IndicatorCode = x.Code, SortNo = x.Extension?.SeSortNo ?? 99 }).ToList();
            getSaleBoundsLineChart.PoIndicatorSorts = loginCompanyInfos.Where(x => (x.Extension?.Po ?? false)).OrderBy(x => x.Extension?.PoSortNo ?? 99).Select(x => new IndicatorSort { IndicatorCode = x.Code, SortNo = x.Extension?.PoSortNo ?? 99 }).ToList();
            var idcTableData = await httpClientHelper.Post<TableData<GetSaleAmountAndReturnMoneyResp>>(configuration.GetValue<string>(Define.IDCApi) + "/api/SalesInvoice/GetSaleAmountAndReturnMoney", GetSaleAmountAndReturnMoneyReq, idcToken);
            if(idcTableData.Code  == 200)
            {
                
                //当月累计交货金额
                var saleMoneyData = idcTableData.Data.saleAmountAndDates.Where(x=>x.SaleAmount != 0)
                    .GroupBy(x => x.Date.ToString(Define.YearMonth))
                    .Select(g => new GetSaleBoundsLineChartItem
                    {
                        Month = g.Key,
                        Money = g.Sum(x => x.SaleAmount),
                        indicatorAmountItems = g
                        .GroupBy(x=> x.Indicator )
                        .Select(x=>new GetSaleBoundsLineChartItem.IndicatorAmountItem
                        {
                            Indicator = x.Key,
                            IndicatorName =saleOidcs.FirstOrDefault(c=>c.Code == x.Key)?.Name ?? string.Empty,
                            Amount = x.Sum(s=>s.SaleAmount)
                        }).ToList()
                    })
                    .ToList();
            
                //当月累计收款金额
                var inMoneyData = idcTableData.Data.InMoneyAndDates
                    .Select(x=> new
                    {
                        Date = x.Date,
                        InMoney = x.InMoney,
                        Indicator = acctCodes.FirstOrDefault(a=>a.AccountCode ==  x.AcctCode)?.Indicator
                    }) //转换成带有标识的匿名类
                    .GroupBy(x => x.Date.ToString(Define.YearMonth))
                    .Select(g => new GetSaleBoundsLineChartItem
                    {
                        Month = g.Key,
                        Money = g.Sum(x => x.InMoney),
                        indicatorAmountItems= g
                        .GroupBy(x => x.Indicator)
                        .Select(x => new GetSaleBoundsLineChartItem.IndicatorAmountItem
                        {
                            Indicator = x.Key,
                            IndicatorName = crmOidcs.FirstOrDefault(c => c.Code == x.Key)?.Name ?? string.Empty,
                            Amount = x.Sum(s => s.InMoney )
                        }).ToList()
                    })
                    .ToList();
            
                var refundMoneyData = refundMoneyResp.GroupBy(x => x.Date.ToString(Define.YearMonth))
                    .Select(g => new GetSaleBoundsLineChartItem
                    {
                        Month = g.Key,
                        Money = - g.Sum(x => x.RefundMoney),  //退货金额取负数 方便后面Sun计算
                         indicatorAmountItems = g
                        .GroupBy(x => x.Indicator)
                        .Select(x => new GetSaleBoundsLineChartItem.IndicatorAmountItem
                        {
                            Indicator = x.Key,
                            IndicatorName = saleOidcs.FirstOrDefault(c => c.Code == x.Key)?.Name ?? string.Empty,
                            Amount = - x.Sum(s => s.RefundMoney)
                        }).ToList()
                    })
                    .ToList();
                //销售额=当月累计交货金额-当月累计退货金额.
                getSaleBoundsLineChart.SaleMoneyData = new List<GetSaleBoundsLineChartItem>();
                getSaleBoundsLineChart.ReturnMoneyData = new List<GetSaleBoundsLineChartItem>();
                foreach (var month in months)
                {
                    getSaleBoundsLineChart.SaleMoneyData.Add(new GetSaleBoundsLineChartItem { Month = month, Money = (saleMoneyData.FirstOrDefault(x=>x.Month == month)?.Money ?? 0M ), indicatorAmountItems = (saleMoneyData.FirstOrDefault(x => x.Month == month)?.indicatorAmountItems ?? new List<GetSaleBoundsLineChartItem.IndicatorAmountItem>() ).GroupBy(x => new { x.IndicatorName ,x.Indicator}).Select(x => new GetSaleBoundsLineChartItem.IndicatorAmountItem
                    {
                        Indicator = x.Key.Indicator,
                        IndicatorName = x.Key.IndicatorName,
                        Amount = x.Sum(s => s.Amount)
                    }).OrderByDescending(x => x.Amount).ToList()});
                }
                
                foreach(var chartItem in getSaleBoundsLineChart.SaleMoneyData.Select(x=>x.indicatorAmountItems))
                {
                    chartItem.AddRange(saleOidcs.Where(x => !chartItem.Exists(i => i.IndicatorName == x.Name)).Select(x => new GetSaleBoundsLineChartItem.IndicatorAmountItem
                    {
                        Indicator = x.Code,
                        IndicatorName = x.Name,
                        Amount = 0
                    }));
                }
                
                //回款额 = 当月累计收款金额 - 当月累计退款金额.
                foreach (var month in months)
                {
                    var thisMonthInData = inMoneyData.FirstOrDefault(x => x.Month == month);
                    var thisMonthRefundData = refundMoneyData.FirstOrDefault(x => x.Month == month);
                    var thisMonthindicatorAmountItems = new List<GetSaleBoundsLineChartItem.IndicatorAmountItem>();
                    if(thisMonthInData != null)
                    {
                        thisMonthindicatorAmountItems.AddRange(thisMonthInData.indicatorAmountItems);
                    }

                    if(thisMonthRefundData != null)
                    {
                        thisMonthindicatorAmountItems.AddRange(thisMonthRefundData.indicatorAmountItems);
                    }
                    
                    getSaleBoundsLineChart.ReturnMoneyData.Add(new GetSaleBoundsLineChartItem { Month = month, Money = (thisMonthInData?.Money ?? 0M) + (thisMonthRefundData?.Money ?? 0M) ,indicatorAmountItems = thisMonthindicatorAmountItems.GroupBy(x=> new { x.IndicatorName,x.Indicator }).Select(x=> new GetSaleBoundsLineChartItem.IndicatorAmountItem
                    {
                        Indicator = x.Key.Indicator,
                        IndicatorName = x.Key.IndicatorName,
                        Amount = x.Sum(s => s.Amount)
                    }).OrderByDescending(x => x.Amount).ToList()});
                }

                foreach (var chartItem in getSaleBoundsLineChart.ReturnMoneyData.Select(x => x.indicatorAmountItems))
                {
                    chartItem.AddRange(saleOidcs.Where(x => !chartItem.Exists(i => i.IndicatorName == x.Name)).Select(x => new GetSaleBoundsLineChartItem.IndicatorAmountItem
                    {
                        Indicator = x.Code,
                        IndicatorName = x.Name,
                        Amount = 0
                    }));
                }
            }

            var reimPaymentLineChartItem = await Payment_ReimburseLineChartItem(req, buyOidcs, userIds);
            var outsourcPaymentLineChartItem = await Payment_OutsourcLineChartItem(req, buyOidcs, userIds);
            var purchasePaymentLineChartItem = await Payment_PurchasePaymentLineChartItem(req, buyOidcs, userIds);
            getSaleBoundsLineChart.PaymentDate = reimPaymentLineChartItem.Concat(outsourcPaymentLineChartItem).Concat(purchasePaymentLineChartItem).GroupBy(x => x.Month).Select(x => new GetPaymentLineChartItem 
            { 
                Month = x.Key,
                Money = x.Sum(g => g.Money), 
                PayMoney = x.Sum(g => g.PayMoney),
                UnPayMoney = x.Sum(g => g.UnPayMoney), 
                indicatorAmountItems = x.Select(y=>y.indicatorAmountItems)
                .Aggregate(new List<GetSaleBoundsLineChartItem.IndicatorAmountItem>(), (a, b) => 
                {
                    a.AddRange(b);
                    return a;
                })
                .GroupBy(x=>new { x.IndicatorName ,x.Indicator})
                .Select(x=> new GetSaleBoundsLineChartItem.IndicatorAmountItem
                {
                    Indicator = x.Key.Indicator,
                    IndicatorName = x.Key.IndicatorName,
                    Amount = x.Sum(s => s.Amount)
                }).OrderByDescending(x=>x.Amount).ToList()
            }).ToList();
            foreach (var chartItem in getSaleBoundsLineChart.PaymentDate.Select(x => x.indicatorAmountItems))
            {
                chartItem.AddRange(buyOidcs.Where(x => !chartItem.Exists(i => i.IndicatorName == x.Name)).Select(x => new GetSaleBoundsLineChartItem.IndicatorAmountItem
                {
                    Indicator = x.Code,
                    IndicatorName = x.Name,
                    Amount = 0
                }));
            }
            await BuildTaxMoneyDataAsync(req, logincontext, getSaleBoundsLineChart, months, saleOidcs);

            var originalMonths = monthsData.OriginalMonths.Select(x => x.ToString(Define.YearMonth));
            var minus12Months = monthsData.Minus12Months.Select(x => x.ToString(Define.YearMonth));
            return (new Finance.Response.GetSaleBoundsLineChart
            {
                SeIndicatorSorts = getSaleBoundsLineChart.SeIndicatorSorts,
                PoIndicatorSorts = getSaleBoundsLineChart.PoIndicatorSorts,
                ReturnMoneyData = getSaleBoundsLineChart.ReturnMoneyData.Where(x => originalMonths.Contains(x.Month)).OrderBy(x=>x.Month).ToList(),
                PaymentDate = getSaleBoundsLineChart.PaymentDate.Where(x => originalMonths.Contains(x.Month)).OrderBy(x => x.Month).ToList(),
                SaleMoneyData =  getSaleBoundsLineChart.SaleMoneyData.Where(x => originalMonths.Contains(x.Month)).OrderBy(x => x.Month).ToList(),
                TaxMoneyData = getSaleBoundsLineChart.TaxMoneyData.Where(x => originalMonths.Contains(x.Month)).OrderBy(x => x.Month).ToList(),
            }
            ,new Finance.Response.GetSaleBoundsLineChart
            {
                SeIndicatorSorts = getSaleBoundsLineChart.SeIndicatorSorts,
                PoIndicatorSorts = getSaleBoundsLineChart.PoIndicatorSorts,
                ReturnMoneyData = getSaleBoundsLineChart.ReturnMoneyData.Where(x => minus12Months.Contains(x.Month)).OrderBy(x => x.Month).ToList(),
                PaymentDate = getSaleBoundsLineChart.PaymentDate.Where(x => minus12Months.Contains(x.Month)).OrderBy(x => x.Month).ToList(),
                SaleMoneyData = getSaleBoundsLineChart.SaleMoneyData.Where(x => minus12Months.Contains(x.Month)).OrderBy(x => x.Month).ToList(),
                TaxMoneyData = getSaleBoundsLineChart.TaxMoneyData.Where(x => minus12Months.Contains(x.Month)).OrderBy(x => x.Month).ToList(),
            });
        }
        /// <summary>
        /// 判断输入和权限
        /// </summary>
        private async Task<(bool haveRight,List<int> slpCodes,List<string> userIds)> GetSaleBoundsLineChartHanldeReq(GetSaleBoundsLineChartReq req, AuthStrategyContext logincontext)
        {
            var slpCodes = new List<int>();
            var user = logincontext.User;
            var roles = logincontext.Roles;
            var slpInfos1 = await SalePersonDropDownOptions();
            // 4.0 User.Id
            var userIds = new List<string>();
            //转换成SlpCode 作为查询条件 
            switch (req.ChartEnum)
            {
                case Request.GetSaleBoundsLineChart.View_Person:

                    if (req.SearchText.Count > 0)
                    {
                        slpCodes.AddRange(req.SearchText.Select(x => Convert.ToInt32(x)));
                        userIds = slpInfos1.Where(g => req.SearchText.Select(x => Convert.ToInt32(x)).ToList().Contains(Convert.ToInt32(g.Id.ToString())))
                            .Select(s => s.UserIds).Aggregate(new List<string>(), (a, b) => { a.AddRange(b); return a; }).ToList();
                    }
                    else
                    {
                        return (false, slpCodes, userIds);
                    }
                    break;
                case Request.GetSaleBoundsLineChart.View_Dept:
                    var slpDepts = slpInfos1.Select(x =>
                    {
                        var detpSlpNameSplit = x.Name.ToString().Split('-');
                        var deptName = string.Empty;
                        if (detpSlpNameSplit.Length > 1)
                        {
                            deptName = detpSlpNameSplit[0];
                        }
                        return new { SlpCode = Convert.ToInt32(x.Id), Department = deptName };
                    }).Where(x => req.SearchText.Contains(x.Department)).ToList();
                    slpCodes.AddRange(slpDepts.Select(x => x.SlpCode));
                    userIds = slpInfos1.Where(g => slpDepts.Select(x => x.SlpCode).ToList().Contains(Convert.ToInt32(g.Id.ToString())))
                            .Select(s => s.UserIds).Aggregate(new List<string>(), (a, b) => { a.AddRange(b); return a; }).ToList();
                    break;
                default:
                    if (!roles.Exists(x => x.RoleKey == RoleKeyConsts.FICO_HOME_SALEBOUND_ALLDATA) && !roles.Exists(x => x.RoleKey == RoleKeyConsts.FICO_HOME_SALEBOUND_DEPT_PERSON) && req.ChartEnum == Request.GetSaleBoundsLineChart.View_All)
                    {
                        var currentSlp = slpInfos1.FirstOrDefault(x => x.Name.ToString().Contains(user.Name));
                        //只能查看个人的
                        if (currentSlp != null)
                        {
                            slpCodes.Add(Convert.ToInt32(currentSlp.Id));
                        }
                        else
                        {
                            return (false, slpCodes, userIds);
                        }
                    }
                    else if (roles.Exists(x => x.RoleKey == RoleKeyConsts.FICO_HOME_SALEBOUND_DEPT_PERSON) && !roles.Exists(x => x.RoleKey == RoleKeyConsts.FICO_HOME_SALEBOUND_ALLDATA) && req.ChartEnum == Request.GetSaleBoundsLineChart.View_All)
                    {
                        //只能部门内的数据
                        var slpDepts1 = slpInfos1.Select(x =>
                        {
                            var detpSlpNameSplit = x.Name.ToString().Split('-');
                            var deptName = string.Empty;
                            if (detpSlpNameSplit.Length > 0)
                            {
                                deptName = detpSlpNameSplit[0];
                            }
                            return new { SlpCode = Convert.ToInt32(x.Id), Department = deptName };
                        }).Where(x => logincontext.Orgs.FirstOrDefault().Name.Contains(x.Department)).ToList();
                        slpCodes.AddRange(slpDepts1.Select(x => x.SlpCode));
                        userIds = slpInfos1.Where(g => slpDepts1.Select(x => x.SlpCode).ToList().Contains(Convert.ToInt32(g.Id.ToString())))
                          .Select(s => s.UserIds).Aggregate(new List<string>(), (a, b) => { a.AddRange(b); return a; }).ToList();
                    }
                    break;
            }
            return (true, slpCodes, userIds);
        }


        private static bool GetSaleBoundsLineChartHaveAllDataAuthority(AuthStrategyContext logincontext)
        {
            var roles = logincontext.Roles;
            if (roles.Exists(x => x.RoleKey == RoleKeyConsts.FICO_HOME_SALEBOUND_ALLDATA))
            {
                return true;
            }
            return false;
        }

        /// <summary>
        /// 构建税额数据
        /// </summary>
        private async Task BuildTaxMoneyDataAsync(GetSaleBoundsLineChartReq req, AuthStrategyContext logincontext, Response.GetSaleBoundsLineChart getSaleBoundsLineChart, IEnumerable<string> months, List<crm_oidc> saleOidcs)
        {
            getSaleBoundsLineChart.TaxMoneyData = new List<GetSaleBoundsLineChartItem>();
            if (GetSaleBoundsLineChartHaveAllDataAuthority(logincontext))
            {
                // 查税额
                var taxResp = await taxReportApp.GetTaxSummaryByMonthRangeAsync(req.BeginTime.ToString("yyyy.MM"), req.EndTime.ToString("yyyy.MM"));
                
                var taxData = taxResp.Code == 200 && taxResp.Result != null ? taxResp.Result.Where(x => x.project == "小计").ToList() : new List<TaxReportMonthRangeResponse>();

                foreach (var month in months)
                {
                    var taxMonthKey = month.Replace("-", ".");
                    var monthTaxItems = taxData.Select(x => new GetSaleBoundsLineChartItem.IndicatorAmountItem
                    {
                        Indicator = x.signOrderStr,
                        IndicatorName = x.sign,
                        Amount = x.GetMonthAmounts().TryGetValue(taxMonthKey, out var amt) ? Convert.ToDecimal(amt) : 0M
                    }).ToList();

                    getSaleBoundsLineChart.TaxMoneyData.Add(new GetSaleBoundsLineChartItem
                    {
                        Month = month,
                        Money = monthTaxItems.Sum(x => x.Amount),
                        indicatorAmountItems = monthTaxItems.OrderByDescending(x => x.Amount).ToList()
                    });
                }

                foreach (var chartItem in getSaleBoundsLineChart.TaxMoneyData.Select(x => x.indicatorAmountItems))
                {
                    chartItem.AddRange(saleOidcs.Where(x => !chartItem.Exists(i => i.IndicatorName == x.Name)).Select(x => new GetSaleBoundsLineChartItem.IndicatorAmountItem
                    {
                        Indicator = x.Code,
                        IndicatorName = x.Name,
                        Amount = 0
                    }));
                }
            }
        }

        /// <summary>
        /// 获取采购付款审核步骤
        /// </summary>
        /// <returns></returns>
        private List<WfStep> GetWfSteps()
        {
            var wfSteps = cacheContext.Get<List<WfStep>>("WmsPayPurchaseAuditStep");
            if (wfSteps == null)
            {
                wfSteps = UnitWork.Find<WfStep>(x => x.WfId == 16).ToListAsync().Result;
                cacheContext.Set<List<WfStep>>("WmsPayPurchaseAuditStep", wfSteps, DateTime.Now.AddDays(1));
            }
            return wfSteps;
        }

        /// <summary>
        /// 报销单 当月总经理已审批过但未付出去的 + 当月出纳已付出去的,若非当月则为月份里面付出去的金额
        /// </summary>
        private async Task<List<GetPaymentLineChartItem>> Payment_ReimburseLineChartItem(GetSaleBoundsLineChartReq req, List<crm_oidc> crm_oidcs, List<string> userIds = null)
        {
            var allMonths = GetMonthsBetween(req.BeginTime, req.EndTime).Select(x => x.ToString(Define.YearMonth));
            var getSaleBoundsLineChart = new List<GetPaymentLineChartItem>();
            var serviceRelations = string.Empty;
            if (!string.IsNullOrEmpty(req.Indicator))
            {
                serviceRelations = crm_oidcs.FirstOrDefault(x => x.Code == req.Indicator)?.Name;
            }
            // 获取指定时间范围的最后一个月的上一个月的最后一天
            var lastMonth = req.EndTime.AddMonths(-1);
            var (beginTime, endTime) = GetMonthFirstDayAndFinalDay(req.BeginTime, req.EndTime);
            // 判断最后一个月是不是当月
            if (req.EndTime.Month == DateTime.Now.Month)
            {
                // 获取当月第一天和最后一天
                var (currMonthBeginTime, currMonthEndTime) = GetMonthFirstDayAndFinalDay(DateTime.Now, DateTime.Now);
                (beginTime, endTime) = GetMonthFirstDayAndFinalDay(req.BeginTime, lastMonth);
                var payedReims = await UnitWork.Find<ReimburseInfo>(x => x.PayTime >= currMonthBeginTime && x.PayTime <= currMonthEndTime)
                    .WhereIf(!string.IsNullOrEmpty(serviceRelations), x => x.ServiceRelations == serviceRelations)
                    .WhereIf(userIds != null && userIds.Count > 0, x => userIds.Contains(x.CreateUserId)).Select(x=> new  { x.LocalCurrencyMoney , x.ServiceRelations }).ToListAsync();
                
                // 本月已支付的报销单
                var currMonthPayedReimsMoney = payedReims.Sum(x => x.LocalCurrencyMoney) ?? 0M;
                // 总经理审过的
                // 查询非草稿并且payTime为null的报销单
                var managerAuditedReims = await UnitWork.Find<ReimburseInfo>(x => x.PayTime == null && x.FlowInstanceId != null )
                    .WhereIf(!string.IsNullOrEmpty(serviceRelations), x => x.ServiceRelations == serviceRelations)
                    .WhereIf(userIds != null && userIds.Count > 0, x => userIds.Contains(x.CreateUserId)).Select(x => new { x.LocalCurrencyMoney, x.FlowInstanceId, x.ServiceRelations }).ToListAsync();
                var flowInstanceList = managerAuditedReims.Select(x => x.FlowInstanceId).ToList();
                var query = from f in UnitWork.Find<FlowInstance>(null)
                            join fh in UnitWork.Find<FlowInstanceOperationHistory>(null) on f.Id equals fh.InstanceId
                            where flowInstanceList.Contains(f.Id) && fh.CreateDate>= currMonthBeginTime && fh.CreateDate <= currMonthEndTime
                            select new { f.Id, fh.Content, fh.CreateDate, fh.ApprovalResult };
                var flowHistory = await query.ToListAsync();
                var passFlowIds = new List<string>();
                foreach (var flowGroup in flowHistory.GroupBy(x => x.Id))
                {
                    if (!flowGroup.Any(x => x.ApprovalResult == "驳回") && flowGroup.Any(x => x.Content == managerAudit && x.ApprovalResult == "同意"))
                    {
                        passFlowIds.Add(flowGroup.Key);
                    }
                    else
                    {
                        var lastRejectTime = flowGroup.Where(x => x.ApprovalResult == "驳回").OrderByDescending(x => x.CreateDate).Select(x => x.CreateDate).FirstOrDefault();
                        if (lastRejectTime != null && flowGroup.Where(x => x.CreateDate > lastRejectTime).Any(x => x.Content == managerAudit && x.ApprovalResult == "同意"))
                        {
                            passFlowIds.Add(flowGroup.Key);
                        }
                    }
                }
                
                var passFlowUnPayedReims = managerAuditedReims.Where(x => passFlowIds.Contains(x.FlowInstanceId)).ToList();
                var indicatorAmountItems = payedReims.Select(x => new { Money = x.LocalCurrencyMoney.Value, Indicator = crm_oidcs.FirstOrDefault(o => o.Name == x.ServiceRelations)?.Code })
                    .GroupBy(x => x.Indicator).Select(x => new GetSaleBoundsLineChartItem.IndicatorAmountItem   //已付款的
                    {
                        Indicator = x.Key,
                        IndicatorName = crm_oidcs.FirstOrDefault(o => o.Code == x.Key)?.Name ?? string.Empty,
                        Amount = x.Sum(s => s.Money)
                    })
                    .Concat(passFlowUnPayedReims.Select(x => new { Money = x.LocalCurrencyMoney.Value, Indicator = crm_oidcs.FirstOrDefault(o => o.Name == x.ServiceRelations)?.Code })
                    .GroupBy(x => x.Indicator).Select(x => new GetSaleBoundsLineChartItem.IndicatorAmountItem //总经理审批通过将要付款的
                    {
                        Indicator  = x.Key,
                        IndicatorName = crm_oidcs.FirstOrDefault(o => o.Code == x.Key)?.Name ?? string.Empty,
                        Amount = x.Sum(s => s.Money)
                    }))
                    .GroupBy(x=> new { x.IndicatorName,x.Indicator }).Select(x=> new GetSaleBoundsLineChartItem.IndicatorAmountItem  //根据标识分组重新统计金额
                    { 
                        Indicator = x.Key.Indicator,
                        IndicatorName = x.Key.IndicatorName,
                        Amount = x.Sum(s=>s.Amount)
                    });
               
                var currMonthUnPayMoney = passFlowUnPayedReims.Sum(x => x.LocalCurrencyMoney) ?? 0M;
                getSaleBoundsLineChart.Add(new GetPaymentLineChartItem { Month = req.EndTime.ToString(Define.YearMonth) , Money = currMonthUnPayMoney + currMonthPayedReimsMoney,PayMoney = currMonthPayedReimsMoney  , UnPayMoney = currMonthUnPayMoney,indicatorAmountItems = indicatorAmountItems.ToList() });
              
            }
           
            var allMonthReims  = await UnitWork.Find<ReimburseInfo>(null)
                .Where(x => x.PayTime != null && x.PayTime >= beginTime && x.PayTime <= endTime)
                .WhereIf(userIds != null && userIds.Count > 0, x => userIds.Contains(x.CreateUserId))
                .WhereIf(!string.IsNullOrEmpty(serviceRelations), x => x.ServiceRelations == serviceRelations)
                .Select(g => new { PayMonth = g.PayTime , Money = g.LocalCurrencyMoney.Value , ServiceRelations = g.ServiceRelations }).ToListAsync();
            
            var linecharts = allMonthReims
                .GroupBy(g => g.PayMonth.Value.ToString(Define.YearMonth))
                .Select(g => new GetPaymentLineChartItem { Month = g.Key, Money = g.Sum(x => x.Money) }).ToList();
            
            foreach (var month in allMonths)
            {
                var indicatorAmountItems = allMonthReims.Where(x => x.PayMonth.Value.ToString(Define.YearMonth) == month)
                        .Select(x => new {
                            Money = x.Money,
                            Indicator = crm_oidcs.FirstOrDefault(o => o.Name == x.ServiceRelations)?.Code
                        }).GroupBy(x => x.Indicator)
                        .Select(x => new GetSaleBoundsLineChartItem.IndicatorAmountItem
                        {   
                            Indicator = x.Key,
                            IndicatorName = crm_oidcs.FirstOrDefault(o => o.Code == x.Key)?.Name ?? string.Empty,
                            Amount = x.Sum(s => s.Money)
                        });
                if (req.EndTime.Month == DateTime.Now.Month)
                {
                    if(month != req.EndTime.ToString(Define.YearMonth)) //因为已经统计过当前月份了，所以当前月份的跳过
                    { 
                        getSaleBoundsLineChart.Add(new GetPaymentLineChartItem { Month = month, Money = linecharts.FirstOrDefault(x => x.Month == month)?.Money ?? 0M,
                        indicatorAmountItems = indicatorAmountItems.ToList()});
                    }
                }
                else
                {
                    getSaleBoundsLineChart.Add(new GetPaymentLineChartItem { Month = month, Money = linecharts.FirstOrDefault(x => x.Month == month)?.Money ?? 0M ,
                        indicatorAmountItems = indicatorAmountItems.ToList()
                    });
                }
            }
            return getSaleBoundsLineChart.OrderBy(x=>x.Month).ToList();
        }
        /// <summary>
        /// 服务提成 当月总经理已审批过但未付出去的 + 当月出纳已付出去的,若非当月则为月份里面付出去的金额
        /// </summary>
        private async Task<List<GetPaymentLineChartItem>> Payment_OutsourcLineChartItem(GetSaleBoundsLineChartReq req,List<crm_oidc> crm_Oidcs, List<string> userIds = null)
        {
            var allMonths = GetMonthsBetween(req.BeginTime, req.EndTime).Select(x => x.ToString(Define.YearMonth));
            var getSaleBoundsLineChart = new List<GetPaymentLineChartItem>();
            if(!string.IsNullOrEmpty(req.Indicator) && req.Indicator != crm_Oidcs.FirstOrDefault(x=>x.Name == "东莞新威")?.Code)
            {
                return getSaleBoundsLineChart;
            }
            var (beginTime, endTime) = GetMonthFirstDayAndFinalDay(req.BeginTime, req.EndTime);
            // 判断最后一个月是不是当月
            if (req.EndTime.Month == DateTime.Now.Month)
            {
                // 获取指定时间范围的最后一个月的上一个月的最后一天
                var lastMonth = req.EndTime.AddMonths(-1);
                // 获取当月第一天和最后一天
                var (currMonthBeginTime, currMonthEndTime) = GetMonthFirstDayAndFinalDay(DateTime.Now, DateTime.Now);
                (beginTime, endTime) = GetMonthFirstDayAndFinalDay(req.BeginTime, lastMonth);
                // 本月已支付的报销单
                var currMonthPayedReimsMoney = await UnitWork.Find<Outsourc>(x => x.PayTime >= currMonthBeginTime && x.PayTime <= currMonthEndTime)
                    .WhereIf(userIds != null && userIds.Count > 0, x => userIds.Contains(x.CreateUserId))
                    .SumAsync(x => x.TotalMoney) ?? 0M;
                // 总经理审过的
                // 查询非草稿并且payTime为null的报销单
                var managerAuditedReims = await UnitWork.Find<Outsourc>(x => x.PayTime == null && x.FlowInstanceId != null)
                    .WhereIf(userIds != null && userIds.Count > 0, x => userIds.Contains(x.CreateUserId)).Select(x => new { x.TotalMoney, x.FlowInstanceId }).ToListAsync();
                var flowInstanceList = managerAuditedReims.Select(x => x.FlowInstanceId).ToList();
                var query = from f in UnitWork.Find<FlowInstance>(null)
                            join fh in UnitWork.Find<FlowInstanceOperationHistory>(null) on f.Id equals fh.InstanceId
                            where flowInstanceList.Contains(f.Id) && fh.CreateDate >= currMonthBeginTime && fh.CreateDate <= currMonthEndTime
                            select new { f.Id, fh.Content, fh.CreateDate, fh.ApprovalResult };
                var flowHistory = await query.ToListAsync();
                var passFlowIds = new List<string>();
                foreach (var flowGroup in flowHistory.GroupBy(x => x.Id))
                {
                    if (!flowGroup.Any(x => x.ApprovalResult == "驳回") && flowGroup.Any(x => x.Content == managerAudit && x.ApprovalResult == "同意"))
                    {
                        passFlowIds.Add(flowGroup.Key);
                    }
                    else
                    {
                        var lastRejectTime = flowGroup.Where(x => x.ApprovalResult == "驳回").OrderByDescending(x => x.CreateDate).Select(x => x.CreateDate).FirstOrDefault();
                        if (lastRejectTime != null && flowGroup.Where(x => x.CreateDate > lastRejectTime).Any(x => x.Content == managerAudit && x.ApprovalResult == "同意"))
                        {
                            passFlowIds.Add(flowGroup.Key);
                        }
                    }
                }
                var currMonthUnPayMoney = managerAuditedReims.Where(x => passFlowIds.Contains(x.FlowInstanceId)).Sum(x => x.TotalMoney) ?? 0M;
                getSaleBoundsLineChart.Add(
                    new GetPaymentLineChartItem 
                    { 
                        Month = req.EndTime.ToString(Define.YearMonth),
                        Money = currMonthUnPayMoney + currMonthPayedReimsMoney ,
                        UnPayMoney = currMonthUnPayMoney , 
                        PayMoney = currMonthPayedReimsMoney ,
                        indicatorAmountItems = new List<GetSaleBoundsLineChartItem.IndicatorAmountItem> 
                        { 
                            new GetSaleBoundsLineChartItem.IndicatorAmountItem 
                            { 
                                Indicator = "05",
                                IndicatorName = "东莞新威", 
                                Amount = currMonthUnPayMoney + currMonthPayedReimsMoney 
                            }
                        } 
                    });
            }
            
            var allMonthReims = await UnitWork.Find<Outsourc>(null)
                .Where( x => x.PayTime != null && x.PayTime >= beginTime && x.PayTime <= endTime)
                .WhereIf(userIds != null && userIds.Count > 0, x => userIds.Contains(x.CreateUserId))
                .Select(g => new { PayMonth = g.PayTime, Money = g.TotalMoney.Value }).ToListAsync();
            var linecharts = allMonthReims.GroupBy(g => g.PayMonth.Value.ToString(Define.YearMonth)).Select(g => new GetPaymentLineChartItem { Month = g.Key, Money = g.Sum(x => x.Money) }).ToList();
            foreach (var month in allMonths)
            {
                if (req.EndTime.Month == DateTime.Now.Month && month != req.EndTime.ToString(Define.YearMonth))
                {
                    getSaleBoundsLineChart.Add(new GetPaymentLineChartItem
                    {
                        Month = month,
                        Money = linecharts.FirstOrDefault(x => x.Month == month)?.Money ?? 0M,
                        indicatorAmountItems = new List<GetSaleBoundsLineChartItem.IndicatorAmountItem>
                        {
                            new GetSaleBoundsLineChartItem.IndicatorAmountItem{ Indicator = "05", IndicatorName = "东莞新威" , Amount =linecharts.FirstOrDefault(x => x.Month == month)?.Money ?? 0M }
                        }
                    });
                }
                else if(req.EndTime.Month != DateTime.Now.Month)
                {
                    getSaleBoundsLineChart.Add(new GetPaymentLineChartItem { Month = month, Money = linecharts.FirstOrDefault(x => x.Month == month)?.Money ?? 0M, PayMoney = linecharts.FirstOrDefault(x => x.Month == month)?.Money ?? 0M,
                        indicatorAmountItems = new List<GetSaleBoundsLineChartItem.IndicatorAmountItem>
                            {
                                new GetSaleBoundsLineChartItem.IndicatorAmountItem{ Indicator = "05",IndicatorName = "东莞新威" , Amount =linecharts.FirstOrDefault(x => x.Month == month)?.Money ?? 0M }
                            }
                    });
                }
            }
            return getSaleBoundsLineChart.OrderBy(x => x.Month).ToList();
        }
        /// <summary>
        /// 采购付款 当月总经理已审批过但未付出去的 + 当月出纳已付出去的,若非当月则为月份里面付出去的金额
        /// </summary>
        private async Task<List<GetPaymentLineChartItem>> Payment_PurchasePaymentLineChartItem(GetSaleBoundsLineChartReq req, List<crm_oidc> crm_oidcs, List<string> userIds = null)
        {
       
            var getPaymentLineChartItems = new List<GetPaymentLineChartItem>();
            // 获取当月第一天和最后一天
            var (currMonthBeginTime, currMonthEndTime) = GetMonthFirstDayAndFinalDay(DateTime.Now, DateTime.Now);
            var (beginTime, endTime) = GetMonthFirstDayAndFinalDay(req.BeginTime, req.EndTime);
            var wfSteps = GetWfSteps();
            var receiveAndPayAuditStep = wfSteps.FirstOrDefault(x => x.Name == "财务应用"); //出纳

            var purchasePaymentAuditSteps =await (from pp in UnitWork.Find<PurchasePayment>(null)
                                                join user in UnitWork.Find<WmsUser>(null) on pp.Creator equals user.UserId
                                                join wt in UnitWork.Find<WfTask>(null) on pp.PrePaymentOrder equals wt.Data
                                            join wts in UnitWork.Find<WfTaskStep>(null) on wt.TaskId equals wts.TaskId
                                            where  wts.StepId == receiveAndPayAuditStep.StepId && (wts.Status == (int)WfTaskStepStatus.Pass || wts.Status == (int)WfTaskStepStatus.WaitAudit )
                                            && wts.StatusTime >= beginTime && wts.StatusTime <= endTime  && (userIds == null || userIds.Count <= 0 || userIds.Contains(user.erp4_user_id))
                                                    select new  {
                                                        PrePaymentOrder= pp.PrePaymentOrder,
                                                        CurrencyCode= pp.CurrencyCode,
                                                        DocTotal = pp.DocTotal  ,
                                                        exchange_rate= pp.exchange_rate,
                                                        TaskId= wt.TaskId,
                                                        StepId= wts.StepId,
                                                        CreateTime= wts.CreateTime,
                                                        StatusTime= wts.StatusTime,
                                                        Status= wts.Status ,
                                                        IdentifyId= pp.IdentifyId
                                                    }).ToListAsync();

            var groups = purchasePaymentAuditSteps.Select(x=> new GetPurahcaseQueryItem {
                PrePaymentOrder= x.PrePaymentOrder,
                CurrencyCode= x.CurrencyCode,
                DocTotal=  x.DocTotal ,
                exchange_rate= x.exchange_rate,
                TaskId= x.TaskId,
                StepId= x.StepId,
                CreateTime= x.CreateTime,
                StatusTime= x.StatusTime,
                Status=x.Status,
                IdentifyId = x.IdentifyId.ToString("D2")
            }).ToList();
            if (!string.IsNullOrEmpty(req.Indicator))
            {
                groups = groups.Where(x => x.IdentifyId == req.Indicator).ToList();
            }
            foreach (var group in groups)
            {
                decimal amount = GetConvertedAmount(group);
                var item = getPaymentLineChartItems.Find(x => x.Month == group.StatusTime.Value.ToString(Define.YearMonth));
                if (group.Status == (int)WfTaskStepStatus.Pass)
                {
                    //如果是财务应用阶段且已经通过，计算该金额
                    //如果已有本月金额，则累加
                    if(item != null)
                    {
                        item.Money += amount;
                        //若在当前月份内，赋值payMoney
                        if (IsInCurrentMonth(group.StatusTime.Value,currMonthBeginTime,currMonthEndTime))
                        {
                            item.PayMoney += amount;
                        }
                    }
                    else
                    {
                        //若在当前月份内，赋值payMoney
                        if (IsInCurrentMonth(group.StatusTime.Value, currMonthBeginTime, currMonthEndTime))
                        {
                            getPaymentLineChartItems.Add(new GetPaymentLineChartItem { Month = group.StatusTime.Value.ToString(Define.YearMonth), Money = amount, PayMoney = amount, UnPayMoney = 0 });
                        }
                        else
                        {
                            getPaymentLineChartItems.Add(new GetPaymentLineChartItem { Month = group.StatusTime.Value.ToString(Define.YearMonth), Money = amount });
                        }
                    }
                }
                else if (group.Status == (int)WfTaskStepStatus.WaitAudit)
                {
                    if (item != null) 
                        HandleCurrentMonthUnpayMoney(ref item, group.StatusTime.Value, currMonthBeginTime, currMonthEndTime, amount);
                    else 
                        HandleUnExistMonthUnpayMoney(ref getPaymentLineChartItems, group.StatusTime.Value, currMonthBeginTime, currMonthEndTime, amount);
                }
            }

            var indicatorAmountGroups = groups
                .Where(x=> x.StatusTime <= endTime
                    && x.StatusTime >= beginTime)
                .Select(x => new
                {
                    Indicator = crm_oidcs.FirstOrDefault(y => y.Code == x.IdentifyId)?.Code ?? string.Empty,
                    IndicatorName = crm_oidcs.FirstOrDefault(y => y.Code == x.IdentifyId)?.Name ?? string.Empty,
                    Amount =x.CurrencyCode!= "RMB"? x.DocTotal.Value * x.exchange_rate.Value : x.DocTotal.Value,
                    Month = x.StatusTime.Value.ToString(Define.YearMonth),
                }).ToList();
            foreach(var item in getPaymentLineChartItems)
            {
                item.indicatorAmountItems = indicatorAmountGroups.Where(x => x.Month == item.Month)
                    .GroupBy(x=>new { x.Indicator,x.IndicatorName })
                    .Select(x => new GetSaleBoundsLineChartItem.IndicatorAmountItem 
                    { 
                        Indicator = x.Key.Indicator,
                        IndicatorName = x.Key.IndicatorName, 
                        Amount = x.Sum(s=>s.Amount)
                    }).ToList();
            }

            return getPaymentLineChartItems;
        }

        #region Payment_PurchasePaymentLineChartItem 私有辅助方法
        private static decimal GetConvertedAmount(GetPurahcaseQueryItem group)
        {
            return group.CurrencyCode != "RMB"
                ? group.DocTotal.Value * group.exchange_rate.Value
                : group.DocTotal.Value;
        }
        /// <summary>
        /// 处理本月未付金额
        /// </summary>
        private static void HandleCurrentMonthUnpayMoney(ref GetPaymentLineChartItem item,DateTime statusTime, DateTime beginTime,DateTime endTime,decimal amount)
        {
            if(IsInCurrentMonth(statusTime, beginTime, endTime))
            {
                item.Money += amount;
                item.UnPayMoney += amount;
            }
        }
        /// <summary>
        /// 处理本月未付金额（初始化）
        /// </summary>
        private static void HandleUnExistMonthUnpayMoney(ref List<GetPaymentLineChartItem> items,DateTime statusTime, DateTime beginTime,DateTime endTime,decimal amount)
        {
            if (IsInCurrentMonth(statusTime, beginTime, endTime))
            {
                items.Add(new GetPaymentLineChartItem { Month = statusTime.ToString(Define.YearMonth), Money = amount, PayMoney = 0, UnPayMoney = amount });

            }
        }
        /// <summary>
        /// 是否在本月
        /// </summary>
        private static bool IsInCurrentMonth(DateTime statusTime, DateTime beginTime, DateTime endTime)
        {
            return statusTime >= beginTime && statusTime <= endTime;
        }
        /// <summary>
        /// 查询审批通过和待审批的付款单
        /// </summary>
        sealed class GetPurahcaseQueryItem
        {
            /// <summary>
            /// 汇率
            /// </summary>
            public decimal? exchange_rate { get; set; }
            /// <summary>
            /// 币种
            /// </summary>
            public string CurrencyCode { get; set; }
            /// <summary>
            /// 审批号
            /// </summary>
            public string PrePaymentOrder { get; set; }
            /// <summary>
            /// 金额(外币)
            /// </summary>
            public decimal? DocTotal { get; set; }
            /// <summary>
            /// 审批id
            /// </summary>
            public int TaskId { get; set; }
            /// <summary>
            /// 步骤id
            /// </summary>
            public int StepId { get; set; }
            /// <summary>
            /// 步骤创建时间
            /// </summary>
            public DateTime CreateTime { get; set; }
            /// <summary>
            /// 步骤变更时间
            /// </summary>
            public DateTime? StatusTime { get; set; }
            /// <summary>
            /// 步骤状态
            /// </summary>
            public int Status { get; set; }
            /// <summary>
            /// 标识
            /// </summary>
            public string IdentifyId { get; set; }
        }
        #endregion
        /// <summary>
        /// 获取指定时间范围内的第一天和最后一天 
        /// </summary>
        private static (DateTime beginTime, DateTime endTime) GetMonthFirstDayAndFinalDay(DateTime beginTime, DateTime endTime)
        {
            //处理传入时间参数
            beginTime = new DateTime(beginTime.Year, beginTime.Month, 1,0,0,0,DateTimeKind.Utc);
            //最后一天的最后一分一秒
            endTime = new DateTime(endTime.Year, endTime.Month, DateTime.DaysInMonth(endTime.Year, endTime.Month), 23, 59, 59, DateTimeKind.Utc);

            return (beginTime, endTime);
        }

        /// <summary>
        /// 获取指定时间范围内的 开始月份-12个月 第一天和 结束时间月份的最后一天 
        /// </summary>
        private static (DateTime beginTime, DateTime endTime) GetMonthFirstDayAndFinalDayMinus12Months(DateTime beginTime, DateTime endTime)
        {
            // 计算开始月份减去12个月后的当月第一天
            DateTime adjustedBeginTime = beginTime.AddMonths(-12);
            adjustedBeginTime = new DateTime(adjustedBeginTime.Year, adjustedBeginTime.Month, 1, 0, 0, 0, DateTimeKind.Utc);

            // 计算结束时间月份的最后一天最后一秒
            DateTime adjustedEndTime = new DateTime(endTime.Year, endTime.Month,
                DateTime.DaysInMonth(endTime.Year, endTime.Month), 23, 59, 59, DateTimeKind.Utc);

            return (adjustedBeginTime, adjustedEndTime);
        }


        /// <summary>
        /// 获取指定时间范围内的所有月份
        /// </summary>
        private static List<DateTime> GetMonthsBetween(DateTime startDate, DateTime endDate)
        {
            List<DateTime> months = new List<DateTime>();

            // 设置起始月份的第一天
            DateTime currentMonth = new DateTime(startDate.Year, startDate.Month, 1, 0, 0, 0, DateTimeKind.Utc);

            while (currentMonth <= endDate)
            {
                months.Add(currentMonth);
                // 增加一个月
                currentMonth = currentMonth.AddMonths(1);
            }

            return months;
        }
        /// <summary>
        /// 获取指定时间范围内的所有月份 以及 每个月份-12个月的月份
        /// </summary>
        private static (List<DateTime> OriginalMonths, List<DateTime> Minus12Months) GetMonthsBetweenAndLastYearMonths(DateTime startDate, DateTime endDate)
        {
            List<DateTime> originalMonths = new List<DateTime>();
            List<DateTime> minus12Months = new List<DateTime>();

            // 设置起始月份的第一天
            DateTime currentMonth = new DateTime(startDate.Year, startDate.Month, 1, 0, 0, 0, DateTimeKind.Utc);

            while (currentMonth <= endDate)
            {
                // 添加到原始月份列表
                originalMonths.Add(currentMonth);

                // 计算并添加当前月份-12个月的日期
                DateTime minus12 = currentMonth.AddMonths(-12);
                minus12Months.Add(minus12);

                // 增加一个月
                currentMonth = currentMonth.AddMonths(1);
            }

            return (originalMonths, minus12Months);
        }
        /// <summary>
        /// 获取两个日期之间的月份数
        /// </summary>
        /// <param name="beginTime">开始日期</param>
        /// <param name="endTime">结束日期</param>
        /// <returns>月份数</returns>
        private static int GetMonthsCount(DateTime beginTime, DateTime endTime)
        {
            return ((endTime.Year - beginTime.Year) * 12) + endTime.Month - beginTime.Month + 1;
        }
        /// <summary>
        /// 获取报销单看板数据
        /// </summary>
        /// <param name="req"></param>
        /// <returns></returns>
        public async Task<List<GetReimCardResp>> GetReimburseCardData (GetReimburseCardReq req)
        {
            //获取指定时间范围内的月份的第一天和最后一天
            (req.BeginTime, req.EndTime) = GetMonthFirstDayAndFinalDay(req.BeginTime, req.EndTime);
            //获取指定时间范围内的所有月份
            var months = GetMonthsBetween(req.BeginTime, req.EndTime).Select(x => x.ToString(Define.YearMonth));
            //判断权限 - 只能看自己 、具有查看所有的权限
            var reimInfoQry = UnitWork.Find<ReimburseInfo>(x => x.PayTime != null && x.PayTime >= req.BeginTime && x.PayTime <= req.EndTime);
            var loginContext = _auth.GetCurrentUser();
            
            if (!loginContext.Roles.Exists(x => x.RoleKey == RoleKeyConsts.FICO_HOME_REIM_VIEWALL))
                {
                var userId = loginContext.User.Id;
                reimInfoQry = reimInfoQry.Where(x => x.CreateUserId == userId);
            }
            
            var reimInfos = await reimInfoQry.Select(x => new { x.PayTime, x.CreateUserId, x.LocalCurrencyMoney }).ToListAsync();
            var createUserIds = reimInfos.Select(x => x.CreateUserId).ToList();
            //将部门参数加入
            var userOrgsInfoWithCaIds = await (from u in UnitWork.Find<User>(null) join r in UnitWork.Find<Relevance>(null) on u.Id equals r.FirstId into urs from ur in urs.DefaultIfEmpty()
                           join o in UnitWork.Find<OpenAuth.Repository.Domain.Org>(null) on ur.SecondId equals o.Id where createUserIds.Contains(u.Id) && ur.Key == Define.USERORG select new { u.Id, o.Name, o.CascadeId }).ToListAsync();
            //与4.0 IAuthStrategy 一致的取值方式取得部门信息
            var userOrgsInfos = userOrgsInfoWithCaIds.GroupBy(x => x.Id).Select(g => new
            {
                Id = g.Key,
                OrgName = g.First(x => x.CascadeId == g.Min(y => y.CascadeId)).Name
            }).ToList();

            //初始化所需月份的数据
            var resultDic = new Dictionary<string, GetReimCardDetail>();
            months.ForEach(x => resultDic.TryAdd(x, new GetReimCardDetail { R = 0, CS = 0, Other = 0, S = 0 }));
            var pre_S = nameof(GetReimCardDetail.S);
            var pre_R = nameof(GetReimCardDetail.R);
            var pre_CS = nameof(GetReimCardDetail.CS);
            foreach ( var reimInfo in reimInfos)
            {
                var orgName = userOrgsInfos.FirstOrDefault(x => x.Id == reimInfo.CreateUserId)?.OrgName??string.Empty;
                var month = reimInfo.PayTime.Value.ToString(Define.YearMonth);
                if (orgName.StartsWith(pre_S))
                {
                    resultDic[month].S += reimInfo.LocalCurrencyMoney.Value;
                }
                else if (orgName.StartsWith(pre_R))
                {
                    resultDic[month].R += reimInfo.LocalCurrencyMoney.Value;
                }
                else if (orgName.StartsWith(pre_CS))
                {
                    resultDic[month].CS += reimInfo.LocalCurrencyMoney.Value;
                }
                else
                {
                    resultDic[month].Other += reimInfo.LocalCurrencyMoney.Value;
                }
            }

            var result = resultDic.Where(x=> !(x.Value.S == 0 && x.Value.R==0 && x.Value.CS == 0 && x.Value.Other == 0) ).Select(x => new GetReimCardResp { Time = x.Key, Data = x.Value }).ToList();
            return result;
        }

        /// <summary>
        /// 统计生产提成
        /// </summary>
        /// <param name="startDate"></param>
        /// <param name="endDate"></param>
        /// <returns></returns>
        public async Task<List<ProductionCommissionSummaryResp>> ProductionCommissionSummary(DateTime? startDate, DateTime? endDate)
        {
            if (startDate == null || endDate == null)
            {
                endDate = await UnitWork.Find<SuppliersReconciliation>(x => x.SourceType == PayableInvoiceSourceType.ProductionCommission)
                    .OrderByDescending(x => x.RecDate)
                    .Select(x => x.RecDate)
                    .FirstAsync();

                startDate = endDate.Value.AddMonths(-11);
            }
            var productionCommissions = await UnitWork.Find<SuppliersReconciliation>(x => x.SourceType == PayableInvoiceSourceType.ProductionCommission
                && startDate <= x.RecDate && x.RecDate <= endDate).ToListAsync();

            // 取最新供应商名称
            var cardNameDict = productionCommissions.GroupBy(x => x.CardCode).ToDictionary(x => x.Key, x => x.OrderByDescending(y => y.RecDate).First().CardName);

            var result = new List<ProductionCommissionSummaryResp>();
            for (var month = startDate.Value; month <= endDate; month = month.AddMonths(1))
            {
                result.Add(new ProductionCommissionSummaryResp()
                {
                    Month = month,
                    Items = cardNameDict.Select(x => new ProductionCommissionSummaryItemResp()
                    {
                        CardCode = x.Key,
                        CardName = x.Value,
                        Amount = productionCommissions.FirstOrDefault(y => y.RecDate == month && y.CardCode == x.Key)?.Amount ?? 0M
                    }).ToDictionary(x => x.CardCode)
                });
            }
            return result;
        }

        /// <summary>
        /// 获取服务提成统计数据
        /// </summary>
        /// <param name="startDate"></param>
        /// <param name="endDate"></param>
        /// <returns></returns>
        public async Task<ServiceCommissionStatisticsResp> GetServiceCommissionStatistics(DateTime? startDate, DateTime? endDate)
        {
            // 1. 确定日期范围（默认最近12个月）
            endDate = endDate ?? DateTime.Now;
            startDate = startDate ?? endDate.Value.AddMonths(-11);
            startDate = new DateTime(startDate.Value.Year, startDate.Value.Month, 1,0,0,0, DateTimeKind.Utc);
            endDate = new DateTime(endDate.Value.Year, endDate.Value.Month, DateTime.DaysInMonth(endDate.Value.Year, endDate.Value.Month),0,0,0, DateTimeKind.Utc);

            // 2. 查询已完成的 Outsourc 记录
            var finishedOutsourcs = await UnitWork.Find<Outsourc>(o =>
                o.CurrentProcess == "结束" &&
                o.CreateTime >= startDate &&
                o.CreateTime <= endDate &&
                o.TotalMoney.HasValue)
                .Select(o => new { o.Id, o.CreateTime, o.TotalMoney, o.CreateUserId })
                .ToListAsync();

            if (!finishedOutsourcs.Any())
            {
                var result = new ServiceCommissionStatisticsResp
                {
                    Months = GenerateMonthList(startDate.Value, endDate.Value),
                    Series = new List<CommissionSeries>(),
                    Summary = new CommissionSummary
                    {
                        TotalAmount = 0,
                        TotalRecordCount = 0,
                        MonthCount = 12,
                        TotalDepartmentCount = 0
                    }
                };
                return result;
            }

            var createUserIds = finishedOutsourcs.Select(o => o.CreateUserId).Distinct().ToList();

            // 3. 查询用户绑定关系：CreateUserId → DDUserId
            var ddBindUsers = await UnitWork.Find<DDBindUser>(db =>
                createUserIds.Contains(db.UserId))
                .Select(db => new { db.UserId, db.DDUserId })
                .ToListAsync();

            var ddUserIds = ddBindUsers.Select(db => db.DDUserId).Distinct().ToList();

            // 4. 查询钉钉用户详情：DDUserId → DepartmentCode
            var dingTalkUsers = await UnitWork.Find<DingTalkUserDetail>(dt =>
                ddUserIds.Contains(dt.UserId) && !string.IsNullOrEmpty(dt.DepartmentCode))
                .Select(dt => new { dt.UserId, dt.DepartmentCode })
                .ToListAsync();

            // 5. 构建用户到部门的映射：CreateUserId → DepartmentCode
            var userDeptMap = (from o in finishedOutsourcs.Select(x => x.CreateUserId).Distinct()
                                join db in ddBindUsers on o equals db.UserId into dbGroup
                                from db in dbGroup.DefaultIfEmpty()
                                join dt in dingTalkUsers on db?.DDUserId equals dt.UserId into dtGroup
                                from dt in dtGroup.DefaultIfEmpty()
                                where dt != null
                                select new
                                {
                                    UserId = o,
                                    DeptCode = dt.DepartmentCode
                                }).ToDictionary(x => x.UserId);

            // 6. 关联数据并按月份和部门分组，使用 Outsourc.TotalMoney 作为金额
            var joinedData = finishedOutsourcs
                .Where(o => userDeptMap.ContainsKey(o.CreateUserId))
                .Select(o => new
                {
                    Month = o.CreateTime.Value.ToString("yyyy-MM"),
                    DeptCode = userDeptMap[o.CreateUserId].DeptCode,
                    Amount = o.TotalMoney.Value,
                    OutsourcId = o.Id
                })
                .ToList();

            // 7. 按月份和部门分组统计
            var groupedData = joinedData
                .GroupBy(x => new { x.Month, x.DeptCode })
                .Select(g => new
                {
                    g.Key.Month,
                    g.Key.DeptCode,
                    TotalAmount = g.Sum(x => x.Amount),
                    RecordCount = g.Select(x => x.OutsourcId).Distinct().Count()
                }).ToList();

            // 8. 生成完整的月份列表
            var months = GenerateMonthList(startDate.Value, endDate.Value);

            // 9. 获取所有部门并构建系列数据
            var allDepartments = groupedData
                .Select(d => d.DeptCode)
                .Distinct()
                .OrderBy(d => d)
                .ToList();

            var series = new List<CommissionSeries>();
            foreach (var deptCode in allDepartments)
            {
                var deptData = groupedData
                    .Where(g => g.DeptCode == deptCode)
                    .ToDictionary(g => g.Month);

                var amountData = new List<decimal>();
                var countData = new List<int>();

                foreach (var month in months)
                {
                    if (deptData.ContainsKey(month))
                    {
                        amountData.Add(deptData[month].TotalAmount);
                        countData.Add(deptData[month].RecordCount);
                    }
                    else
                    {
                        amountData.Add(0);
                        countData.Add(0);
                    }
                }

                series.Add(new CommissionSeries
                {
                    DeptmentCode = deptCode,
                    AmountData = amountData,
                    RecordCountData = countData,
                    TotalAmount = amountData.Sum(),
                    TotalRecordCount = countData.Sum()
                });
            }

            // 10. 计算汇总
            var summary = new CommissionSummary
            {
                TotalAmount = series.Sum(s => s.TotalAmount),
                TotalRecordCount = joinedData.Select(x => x.OutsourcId).Distinct().Count(),
                MonthCount = months.Count,
                TotalDepartmentCount = series.Count
            };

            return new ServiceCommissionStatisticsResp
            {
                Months = months,
                Series = series,
                Summary = summary
            };
        }

        /// <summary>
        /// 生成月份列表
        /// </summary>
        /// <param name="startDate">开始时间</param>
        /// <param name="endDate">结束时间</param>
        /// <returns></returns>
        private static List<string> GenerateMonthList(DateTime startDate, DateTime endDate)
        {
            var months = new List<string>();
            var currentMonth = new DateTime(startDate.Year, startDate.Month, 1, 0, 0, 0, DateTimeKind.Utc);
            var lastMonth = new DateTime(endDate.Year, endDate.Month, 1, 0, 0, 0, DateTimeKind.Utc);

            while (currentMonth <= lastMonth)
            {
                months.Add(currentMonth.ToString("yyyy-MM"));
                currentMonth = currentMonth.AddMonths(1);
            }

            return months;
        }

        #region 账龄逾期报表 - 权限校验

        /// <summary>
        /// 账龄逾期报表权限校验（复用salereceivable模块权限）
        /// </summary>
        public async Task<(bool IsNoRight, int Code, string Message)> GetAgingPermission(OverdueAgingChartReq req)
        {
            var loginContext = _auth.GetCurrentUser() ?? throw new CommonException(Define.LoginHasExpired, Define.INVALID_TOKEN);
            var loginUser = loginContext.User;
            var roles = loginContext.Roles.Select(x => x.Id).ToList();

            var moduleElements = await GetAgingModuleElements(roles);

            var domId = GetAgingModuleElementDomId(moduleElements);
            switch (domId)
            {
                case view_all:
                    break;
                case view_dept:
                    var deptResult = await HandleAgingDeptView(loginUser.User_Id, req);
                    if (deptResult.HasError) return deptResult.Result;
                    break;
                case view_self:
                    var selfResult = await HandleAgingSelfView(loginUser.User_Id, req);
                    if (selfResult.HasError) return selfResult.Result;
                    break;
                default:
                    return (true, 500, "用户角色未配置模块权限");
            }

            // 处理公司主体标识权限
            var customQryCategory = await UnitWork.Find<Category>(x => x.TypeId == FinanceConsts.FICO_Custom_Query_Filter).ToListAsync();
            var haveCustomQueryRight = customQryCategory.Select(x => x.DtCode).Intersect(moduleElements.Select(x => x.DomId)).Distinct().ToList();
            if (haveCustomQueryRight.Count > 0)
            {
                var indicators = customQryCategory.Where(x => haveCustomQueryRight.Contains(x.DtCode)).Select(x => x.DtValue).ToList();
                if (req.IndicatorList == null || req.IndicatorList.Count == 0)
                {
                    req.IndicatorList = indicators;
                }
                else if (req.IndicatorList.Except(indicators).Any())
                {
                    var invalidItems = req.IndicatorList.Where(x => !indicators.Contains(x)).ToList();
                    var idcinfo = await UnitWork.Find<crm_oidc>(x => x.sbo_id == Define.SBO_ID).Where(x => invalidItems.Contains(x.Code)).Select(x => x.Name).ToListAsync();
                    return (true, 500, $"无查看{string.Join("，", idcinfo)}标识的权限");
                }
                req.IndicatorList = req.IndicatorList.ConvertAll(i => i == Define.Non_IndicatorCode ? "N" : i);
            }
            else
            {
                var loginInfo = _auth.GetLoginInfo();
                var loginIndicator = loginInfo.IndicateCode;
                if (loginIndicator != Define.All_IndicatorCode)
                {
                    req.Indicator = loginIndicator == Define.Non_IndicatorCode ? "N" : loginIndicator;
                    req.IndicatorList = new List<string>();
                    return (false, 200, string.Empty);
                }
                req.Indicator = req.Indicator == Define.Non_IndicatorCode ? "N" : req.Indicator;
                if (req.IndicatorList != null)
                    req.IndicatorList = req.IndicatorList.ConvertAll(i => i == Define.Non_IndicatorCode ? "N" : i);
            }
            return (false, 200, string.Empty);
        }

        /// <summary>
        /// 获取salereceivable模块元素权限
        /// </summary>
        private async Task<List<ModuleElement>> GetAgingModuleElements(List<string> roleIds)
        {
            var modules = from m in UnitWork.Find<Module>(null)
                          join r in UnitWork.Find<Relevance>(null) on m.Id equals r.SecondId
                          join role in UnitWork.Find<Role>(null) on r.FirstId equals role.Id
                          where r.Key == Define.ROLEMODULE && roleIds.Contains(r.FirstId) && m.Code == FinanceConsts.SalesInvoiceCode
                          select new { m, roleId = role.Id };

            return await (from me in UnitWork.Find<ModuleElement>(null)
                          join r in UnitWork.Find<Relevance>(null) on me.Id equals r.SecondId
                          join role in UnitWork.Find<Role>(null) on r.FirstId equals role.Id
                          join mo in modules on new { roleId = role.Id, MId = me.ModuleId } equals new { mo.roleId, MId = mo.m.Id }
                          where r.Key == Define.ROLEELEMENT && roleIds.Contains(r.FirstId)
                          select me).ToListAsync();
        }

        /// <summary>
        /// 获取当前有效的DomId（view_all > view_dept > view_self）
        /// </summary>
        private static string GetAgingModuleElementDomId(List<ModuleElement> elements)
        {
            if (elements.Exists(x => x.DomId == view_all)) return view_all;
            if (elements.Exists(x => x.DomId == view_dept)) return view_dept;
            return elements.Exists(x => x.DomId == view_self) ? view_self : null;
        }

        /// <summary>
        /// 处理部门查看权限
        /// </summary>
        private async Task<(bool HasError, (bool, int, string) Result)> HandleAgingDeptView(int? userId, OverdueAgingChartReq req)
        {
            if (!userId.HasValue) return (false, default);
            var slpCode = await UnitWork.Find<sbo_user>(x =>
                x.sbo_id == Define.SBO_ID && x.user_id == userId)
                .Select(x => x.sale_id)
                .FirstOrDefaultAsync();
            if (!slpCode.HasValue) return (true, (true, 500, "未配置对应销售员"));
            req.SlpCode = slpCode.Value.ToString();
            var depId = serviceBaseApp.GetSalesDepID(userId.Value);
            var depIds = new List<string> { depId.ToString() };
            req.SlpCodes = (await serviceBaseApp.GetSboSlpCodeIds(depIds, Define.SBO_ID))
                .Select(x => x.ToString()).ToList();
            return (false, default);
        }

        /// <summary>
        /// 处理个人查看权限
        /// </summary>
        private async Task<(bool HasError, (bool, int, string) Result)> HandleAgingSelfView(int? userId, OverdueAgingChartReq req)
        {
            var slpCode = await UnitWork.Find<sbo_user>(x =>
                x.sbo_id == Define.SBO_ID && x.user_id == userId)
                .Select(x => x.sale_id)
                .FirstOrDefaultAsync();
            if (!slpCode.HasValue) return (true, (true, 500, "未配置对应销售员"));
            req.SlpCode = slpCode.Value.ToString();
            req.SlpCodes = new List<string> { slpCode.Value.ToString() };
            return (false, default);
        }

        #endregion

        /// <summary>
        /// 生成账龄档位列头
        /// </summary>
        /// <param name="intervalUnit">间隔单位（"天"/"月"/"期间"）</param>
        /// <param name="req">请求参数（仅"天"模式使用Interval1-4）</param>
        /// <returns>列头列表</returns>
        private List<ColumnHeader> GenerateColumnHeaders(string intervalUnit, OverdueAgingChartReq req)
        {
            var headers = new List<ColumnHeader>();

            if (intervalUnit == "月")
            {
                headers.Add(new ColumnHeader { Field = "bucket1", HeaderName = $"0-{req.Interval1}个月" });
                headers.Add(new ColumnHeader { Field = "bucket2", HeaderName = $"{req.Interval1}-{req.Interval2}个月" });
                headers.Add(new ColumnHeader { Field = "bucket3", HeaderName = $"{req.Interval2}-{req.Interval3}个月" });
                headers.Add(new ColumnHeader { Field = "bucket4", HeaderName = $"{req.Interval3}-{req.Interval4}个月" });
                headers.Add(new ColumnHeader { Field = "bucket5", HeaderName = $"{req.Interval4}个月以上" });
            }
            else if (intervalUnit == "年")
            {
                headers.Add(new ColumnHeader { Field = "bucket1", HeaderName = $"0-{req.Interval1}年" });
                headers.Add(new ColumnHeader { Field = "bucket2", HeaderName = $"{req.Interval1}-{req.Interval2}年" });
                headers.Add(new ColumnHeader { Field = "bucket3", HeaderName = $"{req.Interval2}-{req.Interval3}年" });
                headers.Add(new ColumnHeader { Field = "bucket4", HeaderName = $"{req.Interval3}-{req.Interval4}年" });
                headers.Add(new ColumnHeader { Field = "bucket5", HeaderName = $"{req.Interval4}年以上" });
            }
            else // 默认"天"模式
            {
                headers.Add(new ColumnHeader { Field = "bucket1", HeaderName = $"0-{req.Interval1}天" });
                headers.Add(new ColumnHeader { Field = "bucket2", HeaderName = $"{req.Interval1 + 1}-{req.Interval2}天" });
                headers.Add(new ColumnHeader { Field = "bucket3", HeaderName = $"{req.Interval2 + 1}-{req.Interval3}天" });
                headers.Add(new ColumnHeader { Field = "bucket4", HeaderName = $"{req.Interval3 + 1}-{req.Interval4}天" });
                headers.Add(new ColumnHeader { Field = "bucket5", HeaderName = $"{req.Interval4 + 1}天+" });
            }

            return headers;
        }

        /// <summary>
        /// 根据间隔单位判断到期日期属于哪个账龄档位
        /// </summary>
        /// <param name="agingDate">账龄日期</param>
        /// <param name="dueDate">到期日期</param>
        /// <param name="intervalUnit">间隔单位（"天"/"月"/"年"）</param>
        /// <param name="req">请求参数</param>
        /// <returns>档位编号（1-5）</returns>
        private static int GetAgingBucket(DateTime agingDate, DateTime? dueDate, string intervalUnit, OverdueAgingChartReq req)
        {
            if (!dueDate.HasValue) return 1;
            var due = dueDate.Value.Date;
            var aging = agingDate.Date;

            return intervalUnit switch
            {
                "月" => GetMonthBucket(aging, due, req),
                "年" => GetPeriodBucket(aging, due, req),
                _ => GetDayBucket(aging, due, req)
            };
        }

        /// <summary>
        /// 获取月模式账龄档位（使用Interval1-4作为月数边界）
        /// </summary>
        private static int GetMonthBucket(DateTime aging, DateTime due, OverdueAgingChartReq req)
        {
            if (due >= aging.AddMonths(-req.Interval1)) return 1;
            if (due >= aging.AddMonths(-req.Interval2)) return 2;
            if (due >= aging.AddMonths(-req.Interval3)) return 3;
            if (due >= aging.AddMonths(-req.Interval4)) return 4;
            return 5;
        }

        /// <summary>
        /// 获取期间模式账龄档位（使用Interval1-4作为年数边界）
        /// </summary>
        private static int GetPeriodBucket(DateTime aging, DateTime due, OverdueAgingChartReq req)
        {
            if (due >= aging.AddYears(-req.Interval1)) return 1;
            if (due >= aging.AddYears(-req.Interval2)) return 2;
            if (due >= aging.AddYears(-req.Interval3)) return 3;
            if (due >= aging.AddYears(-req.Interval4)) return 4;
            return 5;
        }

        /// <summary>
        /// 获取天模式账龄档位
        /// </summary>
        private static int GetDayBucket(DateTime aging, DateTime due, OverdueAgingChartReq req)
        {
            var days = (int)(aging - due).TotalDays;
            if (days <= req.Interval1) return 1;
            if (days <= req.Interval2) return 2;
            if (days <= req.Interval3) return 3;
            if (days <= req.Interval4) return 4;
            return 5;
        }

        /// <summary>
        /// 获取账龄逾期柱状图数据（按销售员分组，合并IN/CN/RC三类单据）
        /// </summary>
        /// <param name="req">查询参数</param>
        /// <returns>包含列头和数据的响应</returns>
        public async Task<OverdueAgingChartWrapper> GetOverdueAgingChart(OverdueAgingChartReq req)
        {
            var rawItems = await BuildAgingRawItemsAsync(req);

            if (!req.ShowZeroBalance)
                rawItems = rawItems.Where(x => x.Amount != 0m).ToList();

            var agingDate = req.AgingDate.Date;
            var data = rawItems
                .GroupBy(x => x.SlpName)
                .Select(g => AggregateSalesmanBucket(g, agingDate, req))
                .OrderByDescending(x => x.Total)
                .Take(req.Top)
                .ToList();

            var columnHeaders = GenerateColumnHeaders(req.IntervalUnit, req);

            return new OverdueAgingChartWrapper
            {
                ColumnHeaders = columnHeaders,
                Data = data
            };
        }

        /// <summary>
        /// 聚合销售员的账龄桶数据
        /// </summary>
        private static OverdueAgingChartResp AggregateSalesmanBucket(
            IGrouping<string, AgingRawItem> group,
            DateTime agingDate,
            OverdueAgingChartReq req)
        {
            var resp = new OverdueAgingChartResp { Salesman = group.Key };
            var buckets = new decimal[6]; // 索引1-5对应5个桶

            foreach (var item in group)
            {
                var bucket = GetAgingBucket(agingDate, item.PostingDate, req.IntervalUnit, req);
                buckets[bucket] += item.Amount;
                resp.Total += item.Amount;
            }

            resp.Bucket1 = buckets[1];
            resp.Bucket2 = buckets[2];
            resp.Bucket3 = buckets[3];
            resp.Bucket4 = buckets[4];
            resp.Bucket5 = buckets[5];

            return resp;
        }

        /// <summary>
        /// 获取账龄逾期详情数据（按销售员+客户分组汇总，分页）
        /// </summary>
        /// <param name="req">查询参数</param>
        /// <returns>包含列头和分页明细数据的响应</returns>
        public async Task<OverdueAgingDetailWrapper> GetOverdueAgingDetail(OverdueAgingDetailReq req)
        {
            var rawItems = await BuildAgingRawItemsAsync(req);
            if (!req.ShowZeroBalance)
                rawItems = rawItems.Where(x => x.Amount != 0m).ToList();

            if (!string.IsNullOrEmpty(req.Salesman))
                rawItems = rawItems.Where(x => x.SlpName == req.Salesman).ToList();

            var agingDate = req.AgingDate.Date;
            // 按(销售员, 客户)分组，每条记录根据自身PostingDate确定账龄档位后汇总
            var grouped = rawItems
                .Select(item => new
                {
                    item.SlpName,
                    item.CustomerCode,
                    item.CustomerName,
                    item.Amount,
                    Bucket = GetAgingBucket(agingDate, item.PostingDate, req.IntervalUnit, req)
                })
                .GroupBy(x => new { x.SlpName, x.CustomerCode })
                .Select(g =>
                {
                    var buckets = new decimal[6];
                    decimal total = 0m;
                    string customerName = g.First().CustomerName;
                    foreach (var item in g)
                    {
                        buckets[item.Bucket] += item.Amount;
                        total += item.Amount;
                    }
                    return new OverdueAgingDetailResp
                    {
                        Salesman = g.Key.SlpName,
                        CustomerCode = g.Key.CustomerCode,
                        CustomerName = customerName,
                        DueBalance = total,
                        Bucket1 = buckets[1],
                        Bucket2 = buckets[2],
                        Bucket3 = buckets[3],
                        Bucket4 = buckets[4],
                        Bucket5 = buckets[5]
                    };
                })
                .ToList();

            if (!req.ShowZeroBalance)
                grouped = grouped.Where(x => x.DueBalance != 0m).ToList();

            // 计算每个销售员的总金额用于排序
            var salesmanTotals = grouped
                .GroupBy(x => x.Salesman)
                .ToDictionary(g => g.Key, g => g.Sum(x => x.DueBalance));

            var sorted = grouped
                .OrderByDescending(x => salesmanTotals.ContainsKey(x.Salesman) ? salesmanTotals[x.Salesman] : 0m)
                .ThenBy(x => x.CustomerCode)
                .ToList();

            // 以销售员为单位分页
            var pagedSalesmen = sorted
                .Select(x => x.Salesman)
                .Distinct()
                .Skip((req.Page - 1) * req.PageSize)
                .Take(req.PageSize)
                .ToHashSet();

            var data = sorted.Where(x => pagedSalesmen.Contains(x.Salesman)).ToList();
            var columnHeaders = GenerateColumnHeaders(req.IntervalUnit, req);

            return new OverdueAgingDetailWrapper
            {
                ColumnHeaders = columnHeaders,
                Data = data
            };
        }

        /// <summary>
        /// 构建账龄原始数据列表（合并IN/CN/RC三类单据）
        /// </summary>
        private async Task<List<AgingRawItem>> BuildAgingRawItemsAsync(OverdueAgingChartReq req)
        {
            var result = new List<AgingRawItem>();

            await AddInvoicesAsync(result, req);
            await AddCreditNotesAsync(result, req);
            //await AddReceiptsAsync(result, req, slpDict);
            // 权限过滤：按销售员编码过滤
            if (req.SlpCodes?.Any() == true)
                result = result.Where(x => x.SlpCode.HasValue && req.SlpCodes.Contains(x.SlpCode.Value.ToString())).ToList();

            return result;
        }

        /// <summary>
        /// 添加应收发票数据 (IN)
        /// </summary>
        private async Task AddInvoicesAsync(List<AgingRawItem> result, OverdueAgingChartReq req)
        {
            var query = UnitWork.Find<OINV>(o => o.CANCELED == "N");
            if (!req.ShowReconciledTransactions)
                query = query.Where(o => o.DocStatus == "O");

            if (req.PostingDateFrom.HasValue) query = query.Where(o => o.DocDate >= req.PostingDateFrom.Value);
            if (req.PostingDateTo.HasValue) query = query.Where(o => o.DocDate <= req.PostingDateTo.Value);
            if (req.DueDateFrom.HasValue) query = query.Where(o => o.DocDueDate >= req.DueDateFrom.Value);
            if (req.DueDateTo.HasValue) query = query.Where(o => o.DocDueDate <= req.DueDateTo.Value);
            if (req.DocumentDateFrom.HasValue) query = query.Where(o => o.TaxDate >= req.DocumentDateFrom.Value);
            if (req.DocumentDateTo.HasValue) query = query.Where(o => o.TaxDate <= req.DocumentDateTo.Value);

            if (req.ShowReconciledTransactions)
            {
                // 显示已对账交易：直接使用发票头表总金额
                var data = await (
                    from inv in query
                    join slp in UnitWork.Find<OSLP>(null) on inv.SlpCode equals slp.SlpCode into slpJoin
                
                    from slp in slpJoin.DefaultIfEmpty()
                    select new { SlpName = slp != null ? slp.SlpName : "", inv.SlpCode, inv.CardCode, inv.CardName, inv.DocEntry, inv.DocDueDate, PostingDate = inv.DocDate, inv.DocTotalSy,inv.PaidSys }
                ).ToListAsync();

                result.AddRange(data.Select(x => new AgingRawItem
                {
                    SlpName = x.SlpName,
                    SlpCode = x.SlpCode,
                    CustomerCode = x.CardCode,
                    CustomerName = x.CardName,
                    DocumentNumber = x.DocEntry?.ToString() ?? "",
                    Type = "IN",
                    DueDate = x.DocDueDate,
                    PostingDate = x.PostingDate,
                    Amount = x.DocTotalSy ?? 0
                }));
            }
            else
            {
                // 不显示已对账交易：基于明细行计算实际未清金额
                // 使用 JOIN 替代 Contains 避免超时
                var invoiceWithOpenAmount = await (
                    from inv in query
                    join crd in UnitWork.Find<OCRD>(null) on inv.CardCode equals crd.CardCode
                    join slp in UnitWork.Find<OSLP>(null) on inv.SlpCode equals slp.SlpCode into slpJoin
                    from slp in slpJoin.DefaultIfEmpty()
                    where crd.Balance != 0
                    select new
                    {
                        SlpName = slp.SlpName ?? "",
                        inv.SlpCode,
                        inv.CardCode,
                        inv.CardName,
                        inv.DocEntry,
                        inv.DocDueDate,
                        PostingDate = inv.DocDate,
                        OpenAmount = (inv.DocTotalSy ??0 ) - (inv.PaidSys ?? 0)
                    }
                ).ToListAsync();

                result.AddRange(invoiceWithOpenAmount.Select(x => new AgingRawItem
                {
                    SlpName = x.SlpName,
                    SlpCode = x.SlpCode,
                    CustomerCode = x.CardCode,
                    CustomerName = x.CardName,
                    DocumentNumber = x.DocEntry?.ToString() ?? "",
                    Type = "IN",
                    DueDate = x.DocDueDate,
                    PostingDate = x.PostingDate,
                    Amount = x.OpenAmount
                }).Where(x => x.Amount > 0));
            }
        }

        /// <summary>
        /// 添加应收贷项数据 (CN)
        /// </summary>
        private async Task AddCreditNotesAsync(List<AgingRawItem> result, OverdueAgingChartReq req)
        {
            var query = UnitWork.Find<ORIN>(o => o.CANCELED == "N");
            if (!req.ShowReconciledTransactions)
                query = query.Where(o => o.DocStatus == "O");

            if (req.PostingDateFrom.HasValue) query = query.Where(o => o.DocDate >= req.PostingDateFrom.Value);
            if (req.PostingDateTo.HasValue) query = query.Where(o => o.DocDate <= req.PostingDateTo.Value);
            if (req.DueDateFrom.HasValue) query = query.Where(o => o.DocDueDate >= req.DueDateFrom.Value);
            if (req.DueDateTo.HasValue) query = query.Where(o => o.DocDueDate <= req.DueDateTo.Value);
            if (req.DocumentDateFrom.HasValue) query = query.Where(o => o.TaxDate >= req.DocumentDateFrom.Value);
            if (req.DocumentDateTo.HasValue) query = query.Where(o => o.TaxDate <= req.DocumentDateTo.Value);

            if (req.ShowReconciledTransactions)
            {
                // 显示已对账交易：直接使用贷项头表总金额
                var data = await (
                    from rin in query
                    join slp in UnitWork.Find<OSLP>(null) on rin.SlpCode equals slp.SlpCode into slpJoin
                    from slp in slpJoin.DefaultIfEmpty()
                    select new { SlpName = slp != null ? slp.SlpName : "", rin.SlpCode, rin.CardCode, rin.CardName, rin.DocEntry, rin.DocDueDate, PostingDate = rin.DocDate, rin.DocTotalSy,rin.PaidSys }
                ).ToListAsync();

                result.AddRange(data
                    .Where(x => (x.DocTotalSy ?? 0m) > 0)
                    .Select(x => new AgingRawItem
                    {
                        SlpName = x.SlpName,
                        SlpCode = x.SlpCode,
                        CustomerCode = x.CardCode,
                        CustomerName = x.CardName,
                        DocumentNumber = x.DocEntry?.ToString() ?? "",
                        Type = "CN",
                        DueDate = x.DocDueDate,
                        PostingDate = x.PostingDate,
                        Amount = -(x.DocTotalSy ?? 0)
                    }));
            }
            else
            {
                // 不显示已对账交易：基于明细行计算实际未清金额
                // 使用 JOIN 替代 Contains 避免超时
                var creditWithOpenAmount = await (
                    from rin in query
                    join slp in UnitWork.Find<OSLP>(null) on rin.SlpCode equals slp.SlpCode into slpJoin
                    from slp in slpJoin.DefaultIfEmpty()
                    select new
                    {
                        SlpName = slp.SlpName ?? "",
                        rin.SlpCode,
                        rin.CardCode,
                        rin.CardName,
                        rin.DocEntry,
                        rin.DocDueDate,
                        PostingDate = rin.DocDate,
                        OpenAmount = (rin.DocTotalSy??0) - (rin.PaidSys?? 0) 
                    }
                ).ToListAsync();

                result.AddRange(creditWithOpenAmount.Select(x => new AgingRawItem
                {
                    SlpName = x.SlpName,
                    SlpCode = x.SlpCode,
                    CustomerCode = x.CardCode,
                    CustomerName = x.CardName,
                    DocumentNumber = x.DocEntry?.ToString() ?? "",
                    Type = "CN",
                    DueDate = x.DocDueDate,
                    PostingDate = x.PostingDate,
                    Amount = -x.OpenAmount
                }).Where(x => x.Amount < 0));
            }
        }

        /// <summary>
        /// 添加收款数据 (RC)
        /// </summary>
        private async Task AddReceiptsAsync(List<AgingRawItem> result, OverdueAgingChartReq req, Dictionary<int, string> slpDict)
        {
            var query = UnitWork.Find<ORCT>(null);

            if (req.PostingDateFrom.HasValue) query = query.Where(o => o.DocDate >= req.PostingDateFrom.Value);
            if (req.PostingDateTo.HasValue) query = query.Where(o => o.DocDate <= req.PostingDateTo.Value);
            if (req.DueDateFrom.HasValue) query = query.Where(o => o.DocDueDate >= req.DueDateFrom.Value);
            if (req.DueDateTo.HasValue) query = query.Where(o => o.DocDueDate <= req.DueDateTo.Value);
            if (req.DocumentDateFrom.HasValue) query = query.Where(o => o.TaxDate >= req.DocumentDateFrom.Value);
            if (req.DocumentDateTo.HasValue) query = query.Where(o => o.TaxDate <= req.DocumentDateTo.Value);

            // 不显示已对账交易时，只查询未清金额大于0的收款
            if (!req.ShowReconciledTransactions)
            {
                query = query.Where(o => o.OpenBal > 0);
            }

            await AddReceiptsCaseAAsync(result, query, slpDict, req);
            await AddReceiptsCaseBAsync(result, query, slpDict, req);
        }

        /// <summary>
        /// 添加收款数据 - 情况A: U_XSDD 有值
        /// </summary>
        private async Task AddReceiptsCaseAAsync(List<AgingRawItem> result, IQueryable<ORCT> baseQuery, Dictionary<int, string> slpDict, OverdueAgingChartReq req)
        {
            var data = await (
                from orct in baseQuery.Where(o => o.U_XSDD != null && o.PayNoDoc == "Y" && o.DocTotalSy == o.NoDocSum)
                join ordr in UnitWork.Find<ORDR>(null) on orct.U_XSDD equals ordr.DocEntry into ordrJoin
                from ordr in ordrJoin.DefaultIfEmpty()
                select new { SlpCode = ordr != null ? ordr.SlpCode : (int?)null, orct.CardCode, orct.CardName, orct.DocEntry, orct.DocDueDate, PostingDate = orct.DocDate, orct.DocTotalSy, orct.OpenBal }
            ).ToListAsync();

            result.AddRange(data.Select(x => new AgingRawItem
            {
                SlpName = x.SlpCode.HasValue && slpDict.ContainsKey(x.SlpCode.Value) ? slpDict[x.SlpCode.Value] : "",
                SlpCode = x.SlpCode,
                CustomerCode = x.CardCode,
                CustomerName = x.CardName,
                DocumentNumber = x.DocEntry.ToString(),
                Type = "RC",
                DueDate = x.DocDueDate,
                PostingDate = x.PostingDate,
                Amount = -(req.ShowReconciledTransactions ? (x.DocTotalSy ?? 0m) : (x.OpenBal ?? 0m))
            }));
        }

        /// <summary>
        /// 添加收款数据 - 情况B: U_XSDD没值但 U_U_XSDBCode 有值且没明细关联发票
        /// </summary>
        private async Task AddReceiptsCaseBAsync(List<AgingRawItem> result, IQueryable<ORCT> baseQuery, Dictionary<int, string> slpDict, OverdueAgingChartReq req)
        {
            var data = await (
                from orct in baseQuery.Where(o => o.U_U_XSDBCode != null && o.U_XSDD == null)
                join rct2 in UnitWork.Find<RCT2>(null) on orct.DocNum equals rct2.DocNum into rct2Join
                from rct2 in rct2Join.DefaultIfEmpty()
                join slp in UnitWork.Find<OSLP>(null) on orct.U_U_XSDBCode equals slp.SlpCode.ToString() into slpJoin
                from slp in slpJoin.DefaultIfEmpty()
                where rct2.DocEntry == null
                select new
                {
                    SlpCode = slp != null ? slp.SlpCode : (int?)null,
                    orct.CardCode,
                    orct.CardName,
                    orct.DocEntry,
                    orct.DocDueDate,
                    PostingDate = orct.DocDate,
                    orct.DocTotalSy,
                    orct.OpenBal,
                }).ToListAsync();

            result.AddRange(data.Select(x => new AgingRawItem
            {
                SlpName = x.SlpCode.HasValue && slpDict.ContainsKey(x.SlpCode.Value) ? slpDict[x.SlpCode.Value] : "",
                SlpCode = x.SlpCode,
                CustomerCode = x.CardCode,
                CustomerName = x.CardName,
                DocumentNumber = x.DocEntry.ToString(),
                Type = "RC",
                DueDate = x.DocDueDate,
                PostingDate = x.PostingDate,
                Amount = -(req.ShowReconciledTransactions ? (x.DocTotalSy ?? 0m) : (x.OpenBal ?? 0m))
            }));
        }

        private sealed class AgingRawItem
        {
            public string SlpName { get; set; }
            public int? SlpCode { get; set; }
            public string CustomerCode { get; set; }
            public string CustomerName { get; set; }
            public string DocumentNumber { get; set; }
            public string Type { get; set; }
            public DateTime? DueDate { get; set; }
            public DateTime? PostingDate { get; set; }
            public decimal Amount { get; set; }
        }
    }
}

