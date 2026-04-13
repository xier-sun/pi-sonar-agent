using DocumentFormat.OpenXml.Office.CustomUI;
using DotNetCore.CAP;
using Flurl.Util;
using GlobalModel.Enum;
using Infrastructure;
using Infrastructure.Extensions;
using Infrastructure.Utilities;
using MathNet.Numerics;
using Microsoft.EntityFrameworkCore;
using Microsoft.Extensions.Configuration;
using Npoi.Mapper;
using NStandard;
using OfficeOpenXml;
using OfficeOpenXml.Style;
using OpenAuth.App.CommonHelp;
using OpenAuth.App.CommonHelp.Interfaces;
using OpenAuth.App.Dto.SalesOrder;
using OpenAuth.App.Employees;
using OpenAuth.App.Finance.Dto;
using OpenAuth.App.Finance.Interfaces;
using OpenAuth.App.Finance.Request;
using OpenAuth.App.Finance.Response;
using OpenAuth.App.Flow;
using OpenAuth.App.Interface;
using OpenAuth.App.Order.Enums;
using OpenAuth.App.Order.ModelDto;
using OpenAuth.App.Query.DapperFactory;
using OpenAuth.App.Request;
using OpenAuth.App.Response;
using OpenAuth.App.Serve.ServiceOrders.Dtos;
using OpenAuth.Repository.Domain;
using OpenAuth.Repository.Domain.GlobalEnum;
using OpenAuth.Repository.Domain.SaleOrder;
using OpenAuth.Repository.Domain.Sap;
using OpenAuth.Repository.Domain.Settlement;
using OpenAuth.Repository.Domain.Wms;
using OpenAuth.Repository.Interface;
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.IO;
using System.Linq;
using System.Linq.Dynamic.Core;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using Item = OpenAuth.Repository.Domain.Wms.Item;


namespace OpenAuth.App.Finance
{
    /// <summary>
    /// 
    /// </summary>
    public class FinanceAfterSaleBonusApplicationApp : OnlyUnitWorkBaeApp, IFinanceAfterSaleBonusApplicationApp
    {
        private readonly IFinanceCommonApp _financeCommonApp;
        private readonly IUserDepartMsgHelp _userDepartMsgHelp;
        private readonly IHttpClientHelper _httpClientHelper;
        private readonly IConfiguration _configuration;
        private readonly ServiceBaseApp _serviceBaseApp;
        private readonly FlowInstanceApp _flowInstanceApp;
        private readonly IDapperFactory _dapperFactory;
        private readonly ICapPublisher _capBus;
        private readonly IGlobalIdentificationApp _globalIdentityApp;
        private readonly FinanceAddOnInfoApp _financeAddOnInfoApp;
        private readonly UserManagerApp _userManagerApp;
        private readonly IEmployeeApp _employeeApp;
        private static readonly Regex SaleItemCodeBonusRegex = new Regex(@"^(CJ|MJ)", RegexOptions.IgnoreCase | RegexOptions.Compiled);
        #region 权限
        private const string moduleCode = "afterSaleCommission";
        private const string view_all = "view_all";
        private const string view_dept = "view_dept";
        private const string view_self = "view_self";
        //总表导出权限
        private const string totalSaleBonusExport = "TotalAfterSaleBonusExport";
        private const string BonusFeeDicTypeId = "FinanceBonusFeeType";
        
        private const string SchemeName = "售后提成审批";
        private const string NumberFormat = "#,##0.00";
        #endregion
        /// <summary>
        /// 
        /// </summary>
        /// <param name="globalIdentityApp"></param>
        /// <param name="financeCommonApp"></param>
        /// <param name="unitWork"></param>
        /// <param name="auth"></param>
        /// <param name="userDepartMsgHelp"></param>
        /// <param name="httpClientHelper"></param>
        /// <param name="configuration"></param>
        /// <param name="serviceBaseApp"></param>
        /// <param name="flowInstanceApp"></param>
        /// <param name="dapperFactory"></param>
        /// <param name="capBus"></param>
        /// <param name="userManagerApp"></param>
        /// <param name="employeeApp"></param>
        /// <param name="financeAddOnInfoApp"></param>
        public FinanceAfterSaleBonusApplicationApp(IGlobalIdentificationApp globalIdentityApp, IFinanceCommonApp financeCommonApp, 
            IUnitWork unitWork, IAuth auth, IUserDepartMsgHelp userDepartMsgHelp, IHttpClientHelper httpClientHelper, 
            IConfiguration configuration, ServiceBaseApp serviceBaseApp, FlowInstanceApp flowInstanceApp, IDapperFactory dapperFactory, 
            ICapPublisher capBus, UserManagerApp userManagerApp, IEmployeeApp employeeApp, FinanceAddOnInfoApp financeAddOnInfoApp) : base(unitWork, auth)
        {
            _financeCommonApp = financeCommonApp;
            _userDepartMsgHelp = userDepartMsgHelp;
            _httpClientHelper = httpClientHelper;
            _configuration = configuration;
            _serviceBaseApp = serviceBaseApp;
            _flowInstanceApp = flowInstanceApp;
            _dapperFactory = dapperFactory;
            _capBus = capBus;
            _globalIdentityApp = globalIdentityApp;
            _userManagerApp = userManagerApp;
            _employeeApp = employeeApp;
            _financeAddOnInfoApp = financeAddOnInfoApp;
        }

        /// <summary>
        /// 列表查询
        /// </summary>
        /// <param name="req"></param>
        /// <returns></returns>
        public async Task<TableData<List<FinanceAfterSaleBonusLoadDto>, dynamic>> Load(FinanceSaleBonusLoadReq req)
        {
            var user = _auth.GetCurrentUser().User;
            var userRoleIds = _auth.GetCurrentUser().Roles.Select(x => x.Id).ToList();
            var moduleElements = await _financeCommonApp.GetModuleElementByRoleIds(userRoleIds, moduleCode);
            var systemUserInfos = await _financeCommonApp.GetSystemUserInfos();
            var result = new TableData<List<FinanceAfterSaleBonusLoadDto>, dynamic> { Data = new List<FinanceAfterSaleBonusLoadDto>() };
            if (!await HandleUserPermissions(moduleElements, user.User_Id, req, result, systemUserInfos))
            {
                return result;
            }
            await ResearchConditionTransfer(req);
            if (req.ApplicationStatuss.Contains(BonusApplicationStatus.未申请))
            {
                result.Data.AddRange(await GetWaitApplyedSaleBonus(req, systemUserInfos));
            }
            result.Data.AddRange(await GetAppliedSaleBonus(req, systemUserInfos));
            result.Data = result.Data.OrderByDescending(x => x.OrderUpdateTime).ToList();
            // 补充移交单/业务员/售后主管等信息 & 筛选查看列表
            await PopulateSaleBonusAdditionalData(result.Data, req);

            result.Count = result.Data.Count;
            result.Data = result.Data.Skip((req.page - 1) * req.limit).Take(req.limit).ToList();
            //客户外挂信息
            await SetClientInfos(result.Data);
            //进度条
            await SetBonusProgressNodes(result.Data);
            result.ExtraData = SaleBonusLoadCaculateTotal(result.Data);
            await SetClientInfos(result.Data);
            return result;
        }

        /// <summary>
        /// 提成申请进度条
        /// </summary>
        /// <param name="financeSaleBonusLoadDtos"></param>
        /// <returns></returns>
        private async Task SetBonusProgressNodes(List<FinanceAfterSaleBonusLoadDto> financeSaleBonusLoadDtos)
        {
            var saleOrdrNos = financeSaleBonusLoadDtos.Select(x => x.DocEntry.Value).Distinct().ToList();
            var ordrTimeInfos = (await (from a in UnitWork.Find<ORDR>(null)
                                        join b in UnitWork.Find<OrdrStat>(null) on a.DocEntry.ToString() equals b.Code
                                        where saleOrdrNos.Contains(a.DocEntry)
                                        select new { a.DocEntry, a.CreateDate, b.U_Receipt_LastDate, b.U_Delivery_LastDate, b.U_Bill_LastDate, }).ToListAsync()).ToDictionary(x => x.DocEntry, x => x);


            var saleBonusIds = financeSaleBonusLoadDtos.Where(x => x.SaleBonusId.HasValue).Select(x => x.SaleBonusId).ToList();

            var flowInstanceOperationHistories = await UnitWork.Find<FinanceSaleBonusOperationHis>(null).Where(x => saleBonusIds.Contains(x.SaleBonusId)).ToListAsync();

            financeSaleBonusLoadDtos.ForEach(loadDto =>
            {
                ordrTimeInfos.TryGetValue(loadDto.DocEntry.Value, out var ordrTimeInfo);
                var ordrCreateTime = ordrTimeInfo?.CreateDate;
                var deliveryTime = ordrTimeInfo?.U_Delivery_LastDate;
                var receiptTime = ordrTimeInfo?.U_Receipt_LastDate;
                var billTime = ordrTimeInfo?.U_Bill_LastDate;
                var bonusCreateTime = loadDto.BonusCreateTime;

                var bonusId = loadDto.SaleBonusId;
                var operationHistories = flowInstanceOperationHistories.Where(x => x.SaleBonusId == bonusId).ToList();

                loadDto.ProgressBarNodes.AddRange(GetBeforeProgressBarNodes(ordrCreateTime, receiptTime, billTime, deliveryTime, bonusCreateTime, operationHistories));
            });
        }
        /// <summary>
        /// 
        /// </summary>
        /// <param name="ordrCreateDate"></param>
        /// <param name="receiptTime"></param>
        /// <param name="billTime"></param>
        /// <param name="deliveryTime"></param>
        /// <param name="bonusCreateTime"></param>
        /// <param name="operationHistories"></param>
        /// <returns></returns>
        private static List<ProgressBarNode> GetBeforeProgressBarNodes(DateTime? ordrCreateDate, DateTime? receiptTime, DateTime? billTime, DateTime? deliveryTime,
            DateTime? bonusCreateTime, List<FinanceSaleBonusOperationHis> operationHistories)
        {
            List<ProgressBarNode> progressBarNodeList = new List<ProgressBarNode>();
            progressBarNodeList.Add(new ProgressBarNode
            {
                Index = 1,
                Name = "订单创建->最新交货",
                Value = 1,
                CreateTime = ordrCreateDate,
                IntervalTime = (deliveryTime.HasValue && ordrCreateDate.HasValue) ? (int?)(deliveryTime.Value - ordrCreateDate.Value).TotalSeconds : 0,
                AuditTime = (deliveryTime.HasValue && ordrCreateDate.HasValue) ? (deliveryTime - ordrCreateDate).ToString() : "00:00:00",
                IsPass = true,
                IsCurrentNode = false,
            });
            progressBarNodeList.Add(new ProgressBarNode
            {
                Index = 2,
                Name = "最新交货->最新收款",
                Value = 2,
                CreateTime = deliveryTime,
                IntervalTime = (deliveryTime.HasValue && receiptTime.HasValue) ? (int?)(receiptTime.Value - deliveryTime.Value).TotalSeconds : 0,
                AuditTime = (receiptTime.HasValue && deliveryTime.HasValue) ? (receiptTime - deliveryTime).ToString() : "00:00:00",
                IsPass = true,
                IsCurrentNode = false,
            });
            progressBarNodeList.Add(new ProgressBarNode
            {
                Index = 3,
                Name = "最新交货->最新开票",
                Value = 3,
                CreateTime = billTime,
                IntervalTime = (receiptTime.HasValue && billTime.HasValue) ? (int?)(billTime.Value - receiptTime.Value).TotalSeconds : 0,
                AuditTime = (receiptTime.HasValue && billTime.HasValue) ? (billTime - receiptTime).ToString() : "00:00:00",
                IsPass = true,
                IsCurrentNode = false,
            });
            var receiptOrBillTime = new[] { receiptTime, billTime }.Where(t => t.HasValue).Max();
            progressBarNodeList.Add(new ProgressBarNode
            {
                Index = 4,
                Name = "最新收款/开票->提成创建",
                Value = 4,
                CreateTime = bonusCreateTime,
                IntervalTime = (receiptOrBillTime.HasValue && bonusCreateTime.HasValue) ? (int?)(bonusCreateTime.Value - receiptOrBillTime.Value).TotalSeconds : 0,
                AuditTime = (receiptOrBillTime.HasValue && bonusCreateTime.HasValue) ? (bonusCreateTime - receiptOrBillTime).ToString() : "00:00:00",
                IsPass = true,
                IsCurrentNode = false,
            });
            var financeOperation = operationHistories.OrderByDescending(x => x.CreateTime).FirstOrDefault(x => x.Action == "财务审批");
            var submitOperation = operationHistories.OrderByDescending(x => x.CreateTime).FirstOrDefault(x => x.Action == "提交");
            progressBarNodeList.Add(new ProgressBarNode
            {
                Index = 5,
                Name = "提成创建→审批完成",
                Value = 5,
                CreateTime = financeOperation?.CreateTime,
                IntervalTime = financeOperation?.IntervalTime ?? 0,
                AuditTime = financeOperation != null ? (financeOperation.CreateTime - submitOperation?.CreateTime).ToString() : "00:00:00",
                IsPass = true,
                IsCurrentNode = false,
            });
            return progressBarNodeList;
        }
        /// <summary>
        /// 补充移交单/业务员/售后主管等信息
        /// </summary>
        /// <param name="rows">售后提成数据列表</param>
        /// <param name="req"></param>
        /// <returns></returns>
        private async Task PopulateSaleBonusAdditionalData(List<FinanceAfterSaleBonusLoadDto> rows, FinanceSaleBonusLoadReq req)
        {
            if (rows == null || rows.Count == 0) return;

            var nameOrgDic = _userDepartMsgHelp.GetUserNameOrgDictionary();
            var userMap = await _userManagerApp.GetNsapUsersAsync();
            var slpNames = rows.Select(p => p.SalePerson)
                 .Concat(rows.Select(p => p.CustomerSlpName))
                 .Where(id => id != null)
                 .Distinct()
                 .ToList();
            // 获取SlpCode对应的ERP3 UserId
            var erpUser3IdList = from c in await (from c in UnitWork.Find<crm_oslp>(null) select new { c.SlpCode, c.SlpName }).ToListAsync()
                                 join su in
                                (await (from su in UnitWork.Find<sbo_user>(null) join bu in UnitWork.Find<base_user>(null) on su.user_id equals bu.user_id select new { su.user_id, su.sale_id, bu.user_nm }).ToListAsync()) on c.SlpCode equals su.sale_id
                                 where slpNames.Contains(c.SlpName) && c.SlpName == su.user_nm
                                 select new { su.user_id, su.sale_id };
            var erpUser3IdDic = erpUser3IdList.ToDictionary(x => x.sale_id.Value, x => x.user_id);

            // 获取用户离职信息
            var userIsLeave = await _financeCommonApp.GetUserIsLeave();

            // 设置销售员UserId和离职状态
            rows.ForEach(x =>
            {
                if (erpUser3IdDic.TryGetValue(x.SlpCode, out uint erp3UserId))
                {
                    var nsapUser = userMap.FirstOrDefault(u => u.NsapUserId == erp3UserId);
                    if (nsapUser != null && string.IsNullOrEmpty(x.SalePersonId))
                    {
                        x.SalePersonId = nsapUser.UserID;
                    }
                }
                if (erpUser3IdDic.TryGetValue(x.CustomerSlpCode, out uint erp3UserIdC))
                {
                    x.CustomerSalePersonUserId = userMap.FirstOrDefault(u => u.NsapUserId == erp3UserIdC)?.UserID;
                    x.CustomerErp3UserId = (int?)erp3UserIdC;
                }
                x.UserIsLeave = userIsLeave.FirstOrDefault(y => y.UserId == x.SalePersonId)?.IsLeave ?? false;
            });

            // 处理离职售后单的业务员信息
            var docEntryList = rows.Where(x => x.DocEntry.HasValue).Select(r => r.DocEntry.Value).Distinct().ToList();

            await _financeCommonApp.ProcessAfterSalesOrdersWithSupervisorAsync(new ProcessAfterSalesOrdersParams<FinanceAfterSaleBonusLoadDto>
            {
                DataList = rows,
                GetDocEntry = x => x.DocEntry ?? 0,
                GetIsLeave = x => x.UserIsLeave,
                GetCardCodes = x => x.CardCode,
                SetIsCsOrder = (x, isCs) => x.IsCsOrder = isCs,
                SetErp3UserId = (x, val) => x.CustomerErp3UserId = val ?? 0,
                SetSalePersonUserId = (x, val) => x.CustomerSalePersonUserId = val,
                SetSlpName = (x, val) => x.CustomerSlpName = val,
                SetSlpCode = (x, val) => x.CustomerSlpCode = val
            });

            // 判断移交单
            var handCustomerDocEntrys = await UnitWork.Find<SaleOrderPlugin>(x => x.HandCustomer == HandCustomerEnums.Hand && docEntryList.Contains(x.DocEntry))
                .Select(x => x.DocEntry).ToListAsync();
            rows.Where(x => x.DocEntry.HasValue && handCustomerDocEntrys.Contains(x.DocEntry.Value))
                .ToList()
                .ForEach(x => x.IsMigratedCustomer = true);
            if (req.AuthUserIds != null && req.AuthUserIds.Count > 0)
            {
                rows.RemoveAll(x =>
                !((req.AuthUserIds.Contains(x.SalePersonId))
                || (x.UserIsLeave && req.AuthUserIds.Contains(x.CustomerSalePersonUserId))
                || (handCustomerDocEntrys.Contains(x.DocEntry.Value) && req.AuthUserIds.Contains(x.CustomerSalePersonUserId))
                ));
            }
            // 不离职并且不是移交的单先不需要展示customer的信息
            rows.Where(x => !x.IsMigratedCustomer && !x.UserIsLeave)
                .ToList()
                .ForEach(x =>
                {
                    x.CustomerErp3UserId = 0;
                    x.CustomerSalePersonUserId = "";
                    x.CustomerSlpCode = 0;
                    x.CustomerSlpName = "";
                    x.CustomerSalePersonUserType = "";
                });

            // 获取用户类型（CS还是销售）
            var userIds = rows.Select(p => p.SalePersonId)
                 .Concat(rows.Select(p => p.CustomerSalePersonUserId))
                 .Where(id => !string.IsNullOrEmpty(id))
                 .Distinct()
                 .ToList();

            var userType = await _employeeApp.GetEmployeeTypeAsync(userIds);
            rows.ForEach(x =>
            {
                x.CustomerSalePersonUserType = userType.FirstOrDefault(y => y.UserId == x.CustomerSalePersonUserId)?.UserType;
            });

            // 补充CustomerSlpName的部门前缀
            rows.Where(x => !string.IsNullOrEmpty(x.CustomerSlpName) && !x.CustomerSlpName.Contains("-"))
                .ToList()
                .ForEach(x =>
                {
                    if (nameOrgDic.TryGetValue(x.CustomerSlpName, out var orgName))
                    {
                        x.CustomerSlpName = orgName + "-" + x.CustomerSlpName;
                    }
                });
        }
        /// <summary>
        /// 外挂客户模块信息
        /// </summary>
        /// <param name="result"></param>
        /// <returns></returns>
        private async Task SetClientInfos(List<FinanceAfterSaleBonusLoadDto> result)
        {
            var cardCodes = result.Select(x => x.CardCode).ToList();
            var clientInfos = await _financeAddOnInfoApp.GetClientInfoAsync(cardCodes);
            result.ForEach(x =>
            {
                var canGetValue = clientInfos.TryGetValue(x.CardCode, out var client);
                if (canGetValue)
                {
                    x.ClientInfo = client;
                }

            });
        }
        /// <summary>
        /// 私有方法 - 计算总数
        /// </summary>
        /// <param name="rows"></param>
        /// <returns></returns>
        private static dynamic SaleBonusLoadCaculateTotal(List<FinanceAfterSaleBonusLoadDto> rows)
        {
            return new
            {
                orderTotalAmount = rows.Sum(x => x.OrderAmount), //合同总金额
                deliveryAmount = rows.Sum(x => x.DeliveryAmount), //交货总金额
                billTotalAmount = rows.Sum(x => x.BillAmount) //已开发票金额总计
            };
        }

        /// <summary>
        /// 获取已申请的售后提成
        /// </summary>
        /// <param name="req"></param>
        /// <param name="systemUserInfos"></param>
        /// <returns></returns>
        private async Task<List<FinanceAfterSaleBonusLoadDto>> GetAppliedSaleBonus(FinanceSaleBonusLoadReq req, List<SystemUserInfo> systemUserInfos)
        {
            var oidcDic = await UnitWork.Find<OIDC>(null).ToDictionaryAsync(x => x.Code, x => x.Name);
            var nameOrgDic = _userDepartMsgHelp.GetUserNameOrgDictionary();
            
            var bonusApplications = await UnitWork.Find<FinanceBonusApplication>(null)
                    .Where(x => x.BonusType == Repository.Domain.Settlement.BonusType.售后提成)
                    .WhereIf(req.IndicatorList != null && req.IndicatorList.Count > 0, f => req.IndicatorList.Contains(f.Indicator))
                    .WhereIf(!string.IsNullOrEmpty(req.CardCodeOrCardName), f => f.CardCode.Contains(req.CardCodeOrCardName) || f.CardName.Contains(req.CardCodeOrCardName))
                    .WhereIf(req.DocEntry.HasValue, f => f.OrderNo == req.DocEntry)
                    .WhereIf(req.UserIds != null && req.UserIds.Count > 0, f => req.UserIds.Contains(f.ApplyUserId))
                    .WhereIf(req.ApplicationStatuss != null && req.ApplicationStatuss.Count > 0, f => req.ApplicationStatuss.Contains(f.BonusStatus.Value))
                    .WhereIf(req.SettledDate.HasValue, f => f.SettlementDate.Value.Year == req.SettledDate.Value.Year &&
                                                            f.SettlementDate.Value.Month == req.SettledDate.Value.Month)
                    .ToListAsync();
            
            // 查询客户默认业务员信息
            var cardCodes = bonusApplications.Select(x => x.CardCode).Distinct().ToList();
            var customerSlpInfos = await (from crd in UnitWork.Find<OCRD>(null)
                                          join csp in UnitWork.Find<OSLP>(null) on crd.SlpCode equals csp.SlpCode into cspJoin
                                          from csp in cspJoin.DefaultIfEmpty()
                                          where cardCodes.Contains(crd.CardCode)
                                          select new { crd.CardCode, CustomerSlpCode = crd.SlpCode ?? 0, CustomerSlpName = csp != null ? csp.SlpName : string.Empty })
                                          .ToListAsync();
            var customerSlpDict = customerSlpInfos.GroupBy(x => x.CardCode).ToDictionary(g => g.Key, g => g.First());
            
            // 查询订单对应的SlpCode
            var orderNos = bonusApplications.Where(x => x.OrderNo.HasValue).Select(x => x.OrderNo.Value).ToList();
            var orderSlpInfos = await UnitWork.Find<ORDR>(null)
                .Where(x => orderNos.Contains(x.DocEntry))
                .Select(x => new { x.DocEntry, x.SlpCode,x.CreateDate,x.UpdateDate })
                .ToListAsync();
            var orderSlpDict = orderSlpInfos.ToDictionary(x => x.DocEntry, x => x.SlpCode ?? 0);
            var orderTimeDict = orderSlpInfos.ToDictionary(x => x.DocEntry, x => x);
            return bonusApplications.Select(x => {
                var user = systemUserInfos.FirstOrDefault(s => s.ERP4Id == x.ApplyUserId);
                nameOrgDic.TryGetValue(user?.SlpName ?? "", out string orgName);
                var customerSlpInfo = customerSlpDict.GetValueOrDefault(x.CardCode);
                var orderDateInfo = orderTimeDict.GetValueOrDefault(x.OrderNo.Value);
                return new FinanceAfterSaleBonusLoadDto
                {
                    SaleBonusId = x.Id,
                    Indicator = x.Indicator,
                    IndicatorName = string.IsNullOrEmpty(x.Indicator) ? "" : oidcDic[x.Indicator],
                    CardCode = x.CardCode,
                    CardName = x.CardName,
                    SalePerson = !string.IsNullOrEmpty(orgName) ? orgName + "-" + user?.SlpName ?? "" : user?.SlpName,
                    SalePersonId = x.ApplyUserId,
                    DocEntry = x.OrderNo,
                    OrderAmount = x.OrderAmount,
                    DeliveryAmount = x.DeliveryTotalAmount,
                    ReceiptAmount = x.ReceiptTotalAmount,
                    BillAmount = x.BillTotalAmount,
                    DeductAmount = x.DeductTotalAmount,
                    BonusAmount = x.BonusTotalAmount,
                    Status = x.BonusStatus,
                    StatusName = x.BonusStatus.ToString(),
                    SettlementDate = x.SettlementDate,
                    SettledBatchNo = x.SettledBatchNo,
                    AfterSaleTotalAmount = x.AfterSaleTotalAmount,
                    GlobalApprovalId = x.GlobalApprovalId,
                    // 新增SlpCode和客户默认业务员信息
                    SlpCode = x.OrderNo.HasValue ? orderSlpDict.GetValueOrDefault(x.OrderNo.Value) : 0,
                    CustomerSlpCode = customerSlpInfo?.CustomerSlpCode ?? 0,
                    CustomerSlpName = customerSlpInfo?.CustomerSlpName ?? string.Empty,
                    OrderCreateTime = orderDateInfo?.CreateDate,
                    OrderUpdateTime = orderDateInfo?.UpdateDate,
                    BonusCreateTime = x.CreateTime
                };
            }).ToList();

        }
        /// <summary>
        /// 
        /// </summary>
        /// <param name="req"></param>
        /// <param name="systemUserInfos"></param>
        /// <returns></returns>
        private async Task<List<FinanceAfterSaleBonusLoadDto>> GetWaitApplyedSaleBonus(FinanceSaleBonusLoadReq req, List<SystemUserInfo> systemUserInfos)
        {
            var ordrInfoQuery = from a in UnitWork.Find<ORDR>(null)
                                join b in UnitWork.Find<OrdrStat>(null) on a.DocEntry.ToString() equals b.Code
                                join c in UnitWork.Find<OIDC>(null) on a.Indicator equals c.Code
                                join crd in UnitWork.Find<OCRD>(null) on a.CardCode equals crd.CardCode
                                join csp in UnitWork.Find<OSLP>(null) on crd.SlpCode equals csp.SlpCode into cspJoin
                                from csp in cspJoin.DefaultIfEmpty()
                                where b.U_ReceivePayStatus == 1 && b.U_BillStatus == 2 && a.DocStatus == "C" && a.CANCELED == "N"
                                select new
                                {
                                    Indicator = a.Indicator,
                                    IndicatorName = c.Name,
                                    CardCode = a.CardCode,
                                    CardName = a.CardName,
                                    DocEntry = a.DocEntry,
                                    OrderAmount = a.DocTotal.Value,
                                    DeliveryAmount = b.U_Delivery_TotalAmt ?? 0 - b.U_Credit_TotalAmt ?? 0,
                                    ReceiptAmount = a.U_DocRCTAmount ?? 0 - b.U_Refund_Amt,
                                    BillAmount = b.U_Bill_TotalAmt ?? 0,
                                    SlpCode = a.SlpCode,
                                    CustomerSlpCode = crd.SlpCode ?? 0,
                                    CustomerSlpName = csp != null ? csp.SlpName : string.Empty,
                                    OrdrCreateTime = a.CreateDate,
                                    OrdrUpdateTime = a.UpdateDate
                                };

            var ordrInfos = await ordrInfoQuery.ToListAsync();
            //过滤为cs订单
            var erp3csOrders = await UnitWork.Find<sale_ordr>(x => x.U_ERPFrom == "4"  && x.sbo_id == (int)SboIdEnums.neware_201304 ).Select(x => x.DocEntry).ToListAsync();
            ordrInfos = ordrInfos.Where(x => erp3csOrders.Contains(x.DocEntry)).ToList();
            var exitingOrderNos = await UnitWork.Find<FinanceBonusApplication>(null).Select(x => x.OrderNo).ToListAsync();
            var filteredOrdrInfos = await Task.Run(() =>
               ordrInfos.AsQueryable()
                   .WhereIf(req.IndicatorList != null && req.IndicatorList.Count > 0, x => req.IndicatorList.Contains(x.Indicator))
                   .WhereIf(!string.IsNullOrEmpty(req.CardCodeOrCardName), x =>
                       x.CardCode.Contains(req.CardCodeOrCardName) ||
                       x.CardName.Contains(req.CardCodeOrCardName))
                   .WhereIf(req.DocEntry.HasValue, x => x.DocEntry == req.DocEntry)
                   .WhereIf(exitingOrderNos.Count > 0, x => !exitingOrderNos.Contains(x.DocEntry))
                   .ToList()
            );
            var nameOrgDic = _userDepartMsgHelp.GetUserNameOrgDictionary();

            var result = filteredOrdrInfos.Select(x =>
            {
                var user = systemUserInfos.FirstOrDefault(s => x.SlpCode == s.SlpCode);
                nameOrgDic.TryGetValue(user?.SlpName ?? "", out var orgName);

                return new FinanceAfterSaleBonusLoadDto
                {
                    Indicator = x.Indicator,
                    IndicatorName = x.IndicatorName,
                    CardCode = x.CardCode,
                    CardName = x.CardName,
                    DocEntry = x.DocEntry,
                    OrderAmount = x.OrderAmount,
                    DeliveryAmount = x.DeliveryAmount,
                    ReceiptAmount = x.ReceiptAmount,
                    BillAmount = x.BillAmount,
                    DeductAmount = 0,
                    BonusAmount = 0,
                    Status = BonusApplicationStatus.未申请,
                    StatusName = BonusApplicationStatus.未申请.ToString(),
                    SalePerson = !string.IsNullOrEmpty(orgName) ? orgName + "-" + user?.SlpName : user?.SlpName,
                    SalePersonId = user?.ERP4Id ?? string.Empty,
                    SlpCode = x.SlpCode ?? 0,
                    CustomerSlpCode = x.CustomerSlpCode,
                    CustomerSlpName = x.CustomerSlpName,
                    OrderCreateTime = x.OrdrCreateTime,
                    OrderUpdateTime = x.OrdrUpdateTime
                };
            }).ToList();
            if (req.UserIds != null && req.UserIds.Count > 0)
            {
                result = result.Where(x => req.UserIds.Contains(x.SalePersonId)).ToList();
            }
            return result;

        }


        /// <summary>
        /// 私有方法 - 处理用户权限
        /// </summary>
        /// <param name="moduleElements"></param>
        /// <param name="erp3UserId"></param>
        /// <param name="req"></param>
        /// <param name="tableData"></param>
        /// <param name="systemUserInfos"></param>
        /// <returns></returns>
        private async Task<bool> HandleUserPermissions(List<ModuleElement> moduleElements, int? erp3UserId,
            FinanceSaleBonusLoadReq req, TableData<List<FinanceAfterSaleBonusLoadDto>> tableData, List<SystemUserInfo> systemUserInfos)
        {
            if (moduleElements.Exists(x => x.DomId == view_all))
            {
                //不用筛
            }
            else if (moduleElements.Exists(x => x.DomId == view_dept))
            {
                if (erp3UserId.HasValue)
                {
                    var depid = _serviceBaseApp.GetSalesDepID(erp3UserId.Value);
                    var depids = new List<string>() { depid.ToString() };
                    var slpCodes = (await _serviceBaseApp.GetSboSlpCodeIds(depids, Define.SBO_ID)).Select(x => x).ToList();
                    req.AuthUserIds.AddRange(systemUserInfos.Where(x => slpCodes.Contains(x.SlpCode)).Select(x => x.ERP4Id).ToList());
                }
            }
            else if (moduleElements.Exists(x => x.DomId == view_self))
            {
                var userInfo = systemUserInfos.FirstOrDefault(x => x.ERP3UserId == erp3UserId);
                if (userInfo == null)
                {
                    tableData.Code = 500;
                    tableData.Message = "未配置销售员";
                }
                req.AuthUserIds.Add(userInfo.ERP4Id);
            }
            else
            {
                tableData.Code = 500;
                tableData.Message = "未配置模块权限";
                return false;
            }
            return true;
        }



        /// <summary>
        /// 
        /// </summary>
        /// <param name="req"></param>
        /// <returns></returns>
        private async Task ResearchConditionTransfer(FinanceSaleBonusLoadReq req)
        {
            if (req.ReciptNo.HasValue) req.DocEntry = (await UnitWork.Find<ORCT>(null).Where(x => x.DocEntry == req.ReciptNo.Value).FirstOrDefaultAsync())?.U_XSDD ?? 0;
            if (!string.IsNullOrEmpty(req.SaleMan)) req.UserIds = await UnitWork.Find<User>(null).Where(x => x.Name.Contains(req.SaleMan)).Select(x => x.Id).ToListAsync();
            req.ApplicationStatuss = GetStatusListByStatusCollection(req.StatusCollection);
            //筛选器和下拉框都选了状态，求交集
            if (req.Status.HasValue)
            {
                req.ApplicationStatuss = req.ApplicationStatuss?
                    .Intersect(new List<BonusApplicationStatus> { req.Status.Value })
                    .ToList() ?? new List<BonusApplicationStatus>();
            }

        }


        /// <summary>
        /// 
        /// </summary>
        /// <param name="statusCollection"></param>
        /// <returns></returns>
        private static List<BonusApplicationStatus> GetStatusListByStatusCollection(int? statusCollection)
        {
            return statusCollection switch
            {
                0 => Enum.GetValues(typeof(BonusApplicationStatus)).Cast<BonusApplicationStatus>().ToList(),
                1 => new List<BonusApplicationStatus> { BonusApplicationStatus.未申请, BonusApplicationStatus.草稿 },
                3 => new List<BonusApplicationStatus> { BonusApplicationStatus.已批准 },
                _ => Enum.GetValues(typeof(BonusApplicationStatus)).Cast<BonusApplicationStatus>().ToList()
            };
        }
        /// <summary>
        /// 售后提成审批
        /// </summary>
        /// <param name="saleBonusAccraditationDto"></param>
        /// <returns></returns>
        public async Task<Infrastructure.Response> SaleBonusAccraditation(SaleBonusAccraditationDto saleBonusAccraditationDto)
        {
            Infrastructure.Response result = new Infrastructure.Response();
            if (saleBonusAccraditationDto == null)
            {
                result.Code = 500;
                result.Message = "请求参数不能为空";
                return result;
            }

            var bonusApplication = await UnitWork.Find<FinanceBonusApplication>(f => f.Id == saleBonusAccraditationDto.SaleBonusId && f.BonusType == Repository.Domain.Settlement.BonusType.售后提成).FirstOrDefaultAsync();
            if (bonusApplication == null)
            {
                result.Code = 500;
                result.Message = "售后提成申请信息不存在";
                return result;
            }
            var user = _auth.GetCurrentUser();
            var userDepartment = await (from a in UnitWork.Find<Relevance>(r => r.Key == Define.USERORG)
                                        join c in UnitWork.Find<OpenAuth.Repository.Domain.Org>(null) on a.SecondId equals c.Id
                                        join d in UnitWork.Find<User>(null) on a.FirstId equals d.Id
                                        where user.User.Id == a.FirstId
                                        select new { OrgName = c.Name, a.FirstId, UserName = d.Name }).FirstOrDefaultAsync();
            var flowInstance = await UnitWork.Find<FlowInstance>(f => f.Id == bonusApplication.FlowInstanceId).FirstOrDefaultAsync();

            VerificationReq VerificationReqModle = new VerificationReq
            {
                NodeRejectStep = "",
                NodeRejectType = !saleBonusAccraditationDto.IsReject ? "0" : "1",
                FlowInstanceId = bonusApplication.FlowInstanceId,
                VerificationFinally = !saleBonusAccraditationDto.IsReject ? VerificationFinallyType.agree : VerificationFinallyType.reject,
                VerificationOpinion = !saleBonusAccraditationDto.IsReject ? "同意" : saleBonusAccraditationDto.Remark,
            };
            await _flowInstanceApp.Verification(VerificationReqModle);
            var latestOperationHis = await UnitWork.Find<FinanceSaleBonusOperationHis>(f => f.SaleBonusId == bonusApplication.Id).OrderByDescending(f => f.CreateTime).FirstOrDefaultAsync();
            var currentTime = DateTime.Now;
            using (var transaction = await UnitWork.GetDbContext<FinanceBonusApplication>().Database.BeginTransactionAsync())
            {
                FinanceSaleBonusOperationHis operationHis = new FinanceSaleBonusOperationHis
                {
                    SaleBonusId = bonusApplication.Id,
                    Remark = saleBonusAccraditationDto.Remark,
                    ApprovalResult = !saleBonusAccraditationDto.IsReject ? "同意" : "驳回",
                    IntervalTime = latestOperationHis == null ? 0 : (int)(currentTime - latestOperationHis.CreateTime.Value).TotalSeconds,
                    Action = flowInstance.ActivityName,
                    CreateTime = currentTime,
                    CreateUserId = user?.User.Id,
                    CreateUser = userDepartment?.OrgName + "-" + userDepartment?.UserName,
                };
                await UnitWork.AddAsync<FinanceSaleBonusOperationHis, int>(operationHis);
                if (saleBonusAccraditationDto.IsReject)
                {
                    bonusApplication.BonusStatus = BonusApplicationStatus.已驳回;
                }
                else if (flowInstance.ActivityName == "财务审批")
                {
                    bonusApplication.BonusStatus = BonusApplicationStatus.已批准;
                }
                else
                {
                    bonusApplication.BonusStatus = BonusApplicationStatus.审批中;
                }
                bonusApplication.UpdateTime = currentTime;


                await UnitWork.UpdateAsync(bonusApplication);
                await UnitWork.SaveAsync();
                await transaction.CommitAsync();
            }
            return result;
        }
        /// <summary>
        /// 获取销售单相关费用
        /// </summary>
        /// <param name="docEntry"></param>
        /// <returns></returns>
        public async Task<TableData<List<FinanceBonusFeeResp>, dynamic>> GetSaleBonusRelatedFee(int? docEntry)
        {
            var result = new TableData<List<FinanceBonusFeeResp>, dynamic> { Data = new List<FinanceBonusFeeResp>() };
            if (!docEntry.HasValue)
            {
                result.Code = 500;
                result.Message = "请选择销售订单";
                return result;
            }
            result.Data = (await UnitWork.Find<FinanceBonusFee>(null)
                                .Where(x => x.OrderNo == docEntry)
                                .ToListAsync())
                                .Select(x =>
                                {
                                    return new FinanceBonusFeeResp
                                    {
                                        FeeId = x.Id,
                                        FeeAmount = x.FeeAmount,
                                        FeeType = x.FeeType,
                                        FeeTypeName = x.FeeTypeName,
                                        Remark = x.Remark
                                    };
                                }).ToList();
            var feeTotal = result.Data.Sum(a => a.FeeAmount);
            var ordrInfos = await (from a in UnitWork.Find<ORDR>(null)
                                   join b in UnitWork.Find<OrdrStat>(null) on a.DocEntry.ToString() equals b.Code
                                   where a.DocEntry == docEntry
                                   select new
                                   {
                                       AdjustTotalAmount = feeTotal,
                                       DeliveryAmount = (b.U_Delivery_TotalAmt ?? 0) - (b.U_Credit_TotalAmt ?? 0),
                                       ReceiptAmount = (a.U_DocRCTAmount ?? 0) - b.U_Refund_Amt,
                                   }).FirstOrDefaultAsync();
            result.ExtraData = ordrInfos;
            return result;
        }
        /// <summary>
        /// 
        /// </summary>
        /// <param name="bonusFees"></param>
        /// <returns></returns>
        public async Task<Infrastructure.Response> UpdateSaleBonusRelatedFee(SaleBonusRelatedFeeReq bonusFees)
        {
            if (!bonusFees.DocEntry.HasValue) return new Infrastructure.Response { Code = 500, Message = "请选择销售订单" };
            var bonusInfo = await UnitWork.Find<FinanceBonusApplication>(null).Where(x => x.OrderNo == bonusFees.DocEntry).FirstOrDefaultAsync();
            if (bonusInfo != null && (bonusInfo.BonusStatus == BonusApplicationStatus.已批准 || bonusInfo.BonusStatus == BonusApplicationStatus.已结算))
            {
                return new Infrastructure.Response { Code = 500, Message = "已批准或已结算的订单不允许调整销售费用。" };
            }
            var waitDeleteInfos = await UnitWork.Find<FinanceBonusFee>(null).Where(x => x.OrderNo == bonusFees.DocEntry.Value).ToListAsync();
            await UnitWork.BatchDeleteAsync<FinanceBonusFee>(waitDeleteInfos.ToArray());
            var addInfos = bonusFees.saleBonusFees.Select(x => new FinanceBonusFee
            {
                Id = x.FeeId ?? 0,
                OrderNo = bonusFees.DocEntry.Value,
                FeeAmount = x.FeeAmount,
                FeeType = x.FeeType,
                FeeTypeName = x.FeeTypeName,
                Remark = x.Remark,
                CreateTime = DateTime.Now
            }).ToList();
            await UnitWork.BatchAddAsync<FinanceBonusFee, int>(addInfos.ToArray());
            await UnitWork.SaveAsync();
            return new Infrastructure.Response();

        }
        /// <summary>
        /// 获取销售单费用类型
        /// </summary>
        /// <returns></returns>
        public async Task<TableData> GetBonusFeeTypes()
        {
            var result = new TableData();
            result.Data = await UnitWork.Find<Category>(x => x.TypeId == BonusFeeDicTypeId).Select(x => new { x.DtValue, x.Name }).OrderBy(x => x.DtValue).ToListAsync();
            return result;
        }

        #region 生成售后提成 - 各项扣减金额生成
        /// <summary>
        /// 生成扣减金额配置
        /// </summary>
        /// <param name="docEntry"></param>
        /// <param name="saleBonusItemDetailDtos"></param>
        /// <param name="poItems"></param>
        /// <returns></returns>
        private async Task GenerateItemDeductInfos(int docEntry, List<AfterSaleBonusDetailDto> saleBonusItemDetailDtos
            , List<SaleOrdetRelatedPOItem> poItems)
        {
            var ordr = await UnitWork.Find<ORDR>(null).Where(x => x.DocEntry == docEntry).FirstOrDefaultAsync();
            var ordrCreateDate = ordr.CreateDate;
            var deductCollections = await UnitWork.Find<FinanceDeductionCollection>(x=>x.BonusType == DeductionBonusType.AfterSale)
                .Include(x => x.financeDeductionFixedAmountSettings)
                .Include(x => x.financeDeductionFullDeliverSettings)
                .Include(x => x.financeDeductionMultipleSettings)
                .ToListAsync();
            var settingIds = deductCollections.SelectMany(x => x.financeDeductionFixedAmountSettings).Select(x => x.Id)
                            .Union(deductCollections.SelectMany(x => x.financeDeductionFullDeliverSettings).Select(x => x.Id).ToList())
                            .Union(deductCollections.SelectMany(x => x.financeDeductionFullDeliverSettings).Select(x => x.Id).ToList()).ToList();
            var deductItemInfos = await UnitWork.Find<FinanceDeductionItem>(null).Where(x => settingIds.Contains(x.SettingId)).ToListAsync();

            var deductionFixAmountSettings = deductCollections.SelectMany(x => x.financeDeductionFixedAmountSettings).Where(x => x.StartTime >= ordrCreateDate && x.EndTime <= ordrCreateDate).ToList();
        
            var deductionMultipleSetting = deductCollections.SelectMany(x => x.financeDeductionMultipleSettings).Where(x => x.StartTime >= ordrCreateDate && x.EndTime <= ordrCreateDate).ToList();
            var deductionItems = await UnitWork.Find<FinanceDeductionItem>(null).ToListAsync();
            // 给每个物料加上扣减项目基本信息
            foreach (var item in saleBonusItemDetailDtos)
            {
                item.DeductInfos = deductCollections.Select(x => new ItemDeductInfo
                {
                    DeductionId = x.Id,
                    DeductionName = x.Name,
                }).ToList();
            }

          
            var deductCollectionIds = deductCollections.Select(d => d.Id).ToList();
            var poItemsDic = poItems.GroupBy(x => x.ItemCode).ToDictionary(x=>x.Key);
            // 售后结算单价默认为成本价格 1.2倍 
            foreach (var detailDto in saleBonusItemDetailDtos)
            {
                if(poItemsDic.TryGetValue(detailDto.SaleItemCode,out var goroupPoItems))
                {
                    detailDto.PurUnitPrice = goroupPoItems.Average(x => x.UnitPrice);
                    detailDto.AfterSalesSetPrc = decimal.Round(Math.Max(goroupPoItems.Average(x => x.UnitPrice),detailDto.ItemCost ) * 1.2M, 2 ,MidpointRounding.AwayFromZero);
                    detailDto.AfterSalesSetTotal = decimal.Round(detailDto.AfterSalesSetPrc * detailDto.ItemCount, 2, MidpointRounding.AwayFromZero);
                }
                else
                {
                    detailDto.AfterSalesSetPrc = decimal.Round(detailDto.ItemCost * 1.2M, 2, MidpointRounding.AwayFromZero);
                    detailDto.AfterSalesSetTotal = decimal.Round(detailDto.AfterSalesSetPrc * detailDto.ItemCount, 2, MidpointRounding.AwayFromZero);
                }
            }
            
            foreach (var deductCollection in deductCollections)
            {
                var fixAmountAmtDeduction = deductionFixAmountSettings
                    .Where(x => deductCollectionIds.Contains(x.DeductionCollectionId))
                    .ToList();

                // 计算固定扣减金额（销售单）
                CalculateFixAmountDeductionBySaleOrder(
                    saleBonusItemDetailDtos,
                    fixAmountAmtDeduction.Where(x => x.FormType == DeductionFormTypeEnums.Sale).ToList(),
                    deductionItems
                );

                // 计算成本N倍扣减（采购单据或上次采购价）
                CalculateMultipleDeduction(saleBonusItemDetailDtos,
                    poItems,
                    deductionMultipleSetting.Where(x => x.FormType == DeductionFormTypeEnums.Purchase).ToList(),
                    deductItemInfos
                );

            }


        }
     

      

        #endregion

        #region 生成售后提成 - 基础数据获取
        /// <summary>
        /// 获取相应采购单物料
        /// </summary>
        /// <param name="docEntry"></param>
        /// <returns></returns>
        public async Task<List<SaleOrdetRelatedPOItem>> GetSaleOrdetRelatedPOItems(int docEntry)
        {
            string wmsPurchaseItemSql = @"select p.purchase_order as PurchaseOrder
                                            ,ri.item_code as ItemCode
                                            ,pi.price_unit as UnitPrice
                                            ,pi.item_qty as Quantity
                                            ,p.identify_id as IdentifyId
                                            ,p.identify_name as IdentifyName
                                            from requirement_item ri
                                            inner join purchase_item_requirement pir on ri.requirement_item_id = pir.requirement_item_id
                                            inner join purchase_item pi on pi.purchase_item_id = pir.purchase_item_id and pi.purchase_id = pir.purchase_id
                                            inner join purchase p on pi.purchase_id = p.purchase_id
                                            where ri.source_number = ?docEntry and source_type = 100";
            var _dapperExecutor = _dapperFactory.CreateExecutor(dbContextType: DapperDbContextType.WmsDbContext);
            var purchaseItemInfos = (await _dapperExecutor.QueryAsync<dynamic>(wmsPurchaseItemSql, new { docEntry })).MapToList<SaleOrdetRelatedPOItem>();
            return purchaseItemInfos;
        }
        /// <summary>
        /// 毛利率：订单总金额-物料成本-物料加工费/订单总金额
        /// </summary>
        /// <param name="docEntrys"></param>
        /// <returns></returns>
        public async Task<decimal> GetSaleOrderProfitPercent(List<int> docEntrys)
        {
            var docTotal = (await UnitWork.Find<ORDR>(null).Where(x => docEntrys.Contains(x.DocEntry)).ToListAsync()).Sum(x => x.DocTotal ?? 0);
            try
            {
                var biToken = _auth.GetLoginInfo().Token;
                string url = $"{_configuration.GetValue<string>(Define.BIApi)}/api/Order/SalesOrder/GetDetailItemPriceInfoForFico";
                object req = docEntrys;
                var result = await _httpClientHelper.Post<TableData<List<SaleOrderDetailPriceDto>>>(url, req, biToken);
                if (result.Code == 200)
                {
                    var data = result.Data;
                    var totalCost = data.Sum(d => Math.Round(d.Quantity * (d.StockPrice + d.U_JGF1), 6, MidpointRounding.AwayFromZero));
                    if (docTotal == 0) return 0;
                    return (docTotal - totalCost) / docTotal * 100;
                }
                else return 0m;
            }
            catch (Exception)
            {
                return 0m;
            }
        }
        
        /// <summary>
        /// 获取物料标准价格
        /// </summary>
        /// <param name="itemCodes"></param>
        /// <returns></returns>
        public async Task<Dictionary<string, decimal>> GetItemStandardPrice(List<string> itemCodes)
        {
            if (itemCodes == null || itemCodes.Count <= 0)
            {
                return new Dictionary<string, decimal>();
            }
            var itemInfos = await (from a in UnitWork.Find<Item>(null)
                                   join b in UnitWork.Find<ItemExtendInfo>(null) on a.ItemId equals b.ItemId
                                   where itemCodes.Contains(a.Code)
                                   select new { ItemCode = a.Code, StandardPrice = b.UStandardPrice ?? 0 }).ToListAsync();
            var result = itemInfos.GroupBy(x => x.ItemCode).ToDictionary(x => x.Key, x => x.First().StandardPrice);
            return result;
        }
        /// <summary>
        /// 获取总折扣金额
        /// </summary>
        /// <param name="docEntry"></param>
        /// <returns></returns>
        public async Task<decimal> GetDiscSum(List<int?> docEntry)
        {
            var odlnInofs = await (from a in UnitWork.Find<DLN1>(null)
                                   join b in UnitWork.Find<ODLN>(null) on a.DocEntry equals b.DocEntry
                                   where a.BaseEntry != null && docEntry.Contains(a.BaseEntry)
                                   select new { DiscSum = b.DiscSum, DocEntry = a.BaseEntry }).ToListAsync();
            var discSum = odlnInofs.GroupBy(x => x.DocEntry).Sum(x => x.First().DiscSum ?? 0);
            return discSum;
        }
        #endregion

        #region 生成售后提成 - 判断
        /// <summary>
        /// 是否外贸首单
        /// </summary>
        /// <param name="docEntry"></param>
        /// <returns></returns>
        public async Task<bool> IsFirstForeignOrder(int docEntry)
        {

            var foreignOrder = await (from a in UnitWork.Find<sale_ordr>(null)
                                      join b in UnitWork.Find<crm_crd1>(null) on new { a.CardCode, Address = a.ShipToCode } equals new { b.CardCode, b.Address }
                                      where a.sbo_id == 1 && a.DocEntry == docEntry && a.DocCur != FinanceConsts.RMB
                                          && b.Country != "MO" && b.Country != "TWO" && b.Country != "HK" && b.Country != "CN"
                                      select a).FirstOrDefaultAsync();
            if (foreignOrder == null) return false;
            else
            {
                var firstCustomerDocEntry = await UnitWork.Find<sale_ordr>(null).Where(x => x.sbo_id == Define.SBO_ID && x.CardCode == foreignOrder.CardCode)
                                            .OrderBy(x => x.CreateDate).FirstOrDefaultAsync();
                return firstCustomerDocEntry.DocEntry == docEntry;
            }
        }
       
        #endregion

        #region 生成售后提成 - 配置扣减金额计算方法

        /// <summary>
        /// 销售单-固定扣减金额
        /// </summary>
        /// <param name="saleBonusItemDetailDtos"></param>
        /// <param name="financeDeductionFixedAmountSettings"></param>
        /// <param name="financeDeductionItems"></param>
        /// <returns></returns>
        private static void CalculateFixAmountDeductionBySaleOrder(List<AfterSaleBonusDetailDto> saleBonusItemDetailDtos,
            List<FinanceDeductionFixedAmountSetting> financeDeductionFixedAmountSettings, List<FinanceDeductionItem> financeDeductionItems)
        {
            if (saleBonusItemDetailDtos.Count <= 0 || financeDeductionFixedAmountSettings.Count <= 0) return;
            foreach (var fixDeductItemSetting in financeDeductionFixedAmountSettings)
            {
                var deductItemInfos = financeDeductionItems.Where(x => x.SettingId == fixDeductItemSetting.Id).ToList();
                saleBonusItemDetailDtos.Where(x => deductItemInfos.Select(y => y.ItemCode).Contains(x.SaleItemCode)).ForEach(x => 
                { 
                    x.AfterSalesSetPrc = fixDeductItemSetting.Amount;
                    x.AfterSalesSetTotal = decimal.Round(x.AfterSalesSetPrc * x.ItemCount ,2, MidpointRounding.AwayFromZero);
                });
            }
        }
        
        /// <summary>
        /// 采购单据或上次采购价 - 成本N倍扣减
        /// </summary>
        /// <param name="saleBonusItemDetailDtos"></param>
        /// <param name="relatedPOItems"></param>
        /// <param name="financeDeductionMultipleSettings"></param>
        /// <param name="financeDeductionItems"></param>
        /// <returns></returns>
        private static void CalculateMultipleDeduction(List<AfterSaleBonusDetailDto> saleBonusItemDetailDtos, List<SaleOrdetRelatedPOItem> relatedPOItems, List<FinanceDeductionMultipleSetting> financeDeductionMultipleSettings, List<FinanceDeductionItem> financeDeductionItems)
        {
            foreach (var financeDeductionMultipleSetting in financeDeductionMultipleSettings)
            {
                var deductItemInfos = financeDeductionItems.Where(x => x.SettingId == financeDeductionMultipleSetting.Id).ToDictionary(x=>x.ItemCode);
                foreach (var poItems in relatedPOItems.GroupBy(x=>x.ItemCode))
                {
                    if (deductItemInfos.TryGetValue(poItems.Key, out _))
                    {
                        var saleBonusItem = saleBonusItemDetailDtos.FirstOrDefault(x => x.SaleItemCode == poItems.Key);
                        
                        saleBonusItem.AfterSalesSetPrc = decimal.Round( Math.Max(poItems.Average(x=>x.UnitPrice), saleBonusItem.ItemCost) * financeDeductionMultipleSetting.Multiple, 2,MidpointRounding.AwayFromZero);
                        saleBonusItem.AfterSalesSetTotal = decimal.Round(saleBonusItem.AfterSalesSetPrc * saleBonusItem.ItemCount, 2, MidpointRounding.AwayFromZero);
                    }
                    
                }
            }
        }
        
        
        #endregion

        #region 详情页 - 提成分配详情
        
        #endregion


        #region 自定义售后提成分成
        /// <summary>
        /// 
        /// </summary>
        /// <param name="saleOrder"></param>
        /// <param name="status"></param>
        /// <returns></returns>
        public async Task<TableData<List<FinanceSaleBonusSharingDto>>> GetSaleBonusSharing(int? saleOrder, int? status)
        {
            var result = new TableData<List<FinanceSaleBonusSharingDto>> { Data = new List<FinanceSaleBonusSharingDto>() };
            var bonusSharings = await UnitWork.Find<FinanceSaleBonusShare>(null)
                .WhereIf(status != null, x => x.Status == status.Value)
                .WhereIf(status == null, x => x.Status == 0)
                .WhereIf(saleOrder != null, x => x.SaleOrder == saleOrder.Value)
                .ToListAsync();
            result.Data = bonusSharings.GroupBy(x => x.SaleOrder).Select(x => new FinanceSaleBonusSharingDto
            {
                SaleOrder = x.Key,
                Remark = x.FirstOrDefault().Remark,
                Sharers = x.Select(y => new FinanceSaleBonusSharer
                {
                    ShareUserId = y.ShareUserId,
                    ShareUserName = y.ShareUserName,
                    ShareBonusPercent = y.ShareBonusPercent
                }).ToList()
            }).ToList();
            return result;
        }
        /// <summary>
        /// 新增或更新售后提成分成
        /// </summary>
        /// <param name="financeSaleBonusSharingDto"></param>
        /// <returns></returns>
        /// <exception cref="NotImplementedException"></exception>
        public async Task<Infrastructure.Response> AddOrUpdateSaleBonusSharing(FinanceSaleBonusSharingDto financeSaleBonusSharingDto)
        {
            await CheckSaleBonusSharing(financeSaleBonusSharingDto);
            var financeBonusSharings = await UnitWork.Find<FinanceSaleBonusShare>(null).Where(x => x.SaleOrder == financeSaleBonusSharingDto.SaleOrder).ToListAsync();
            if (financeBonusSharings.Count > 0)
            {
                if (financeBonusSharings.Any(x => x.Status == 1))
                {
                    throw new NotImplementedException("已结算的提成分成信息不可修改");
                }
                await UpdateSaleBonusSharing(financeSaleBonusSharingDto, financeBonusSharings);
            }
            else
            {
                await AddSaleBonusSharing(financeSaleBonusSharingDto);
            }
            return new Infrastructure.Response();
        }
        /// <summary>
        /// 订单提成分成信息删除
        /// </summary>
        /// <param name="saleOrder"></param>
        /// <returns></returns>
        /// <exception cref="NotImplementedException"></exception>
        public async Task<Infrastructure.Response> DeleteSaleBonusSharing(int saleOrder)
        {
            var financeBonusSharings = await UnitWork.Find<FinanceSaleBonusShare>(null).Where(x => x.SaleOrder == saleOrder).ToListAsync();
            if (financeBonusSharings.Count > 0)
            {
                if (financeBonusSharings.Any(x => x.Status == 1))
                {
                    throw new NotImplementedException("已结算的提成分成信息不可删除");
                }
                await UnitWork.BatchDeleteAsync(financeBonusSharings.ToArray());
                await UnitWork.SaveAsync();
            }
            return new Infrastructure.Response();
        }
        /// <summary>
        /// 
        /// </summary>
        /// <param name="financeSaleBonusSharingDto"></param>
        /// <returns></returns>
        private async Task AddSaleBonusSharing(FinanceSaleBonusSharingDto financeSaleBonusSharingDto)
        {
            var user = _auth.GetCurrentUser().User;
            var waitAddSharings = financeSaleBonusSharingDto.Sharers.Select(x =>
            {
                return new FinanceSaleBonusShare
                {
                    SaleOrder = financeSaleBonusSharingDto.SaleOrder.Value,
                    ShareUserId = x.ShareUserId,
                    ShareUserName = x.ShareUserName,
                    ShareBonusPercent = x.ShareBonusPercent,
                    CreateTime = DateTime.Now,
                    UpdateTime = DateTime.Now,
                    CreateUserId = user.Id,
                    Remark = financeSaleBonusSharingDto.Remark,
                    Status = 0
                };
            }).ToList();
            await UnitWork.BatchAddAsync<FinanceSaleBonusShare, int>(waitAddSharings.ToArray());
            await UnitWork.SaveAsync();

        }
        private async Task UpdateSaleBonusSharing(FinanceSaleBonusSharingDto financeSaleBonusSharingDto, List<FinanceSaleBonusShare> existShares)
        {
            var user = _auth.GetCurrentUser().User;
            var newSharings = financeSaleBonusSharingDto.Sharers;
            var waitDeleteSharings = existShares.Where(x => !newSharings.Any(y => y.ShareUserId == x.ShareUserId)).ToList();
            var waitAddSharings = newSharings.Where(x => !existShares.Any(y => y.ShareUserId == x.ShareUserId)).ToList();
            var waitUpdateSharings = existShares.Where(x => newSharings.Any(y => y.ShareUserId == x.ShareUserId)).ToList();

            using (var transaction = await UnitWork.GetDbContext<FinanceSaleBonusShare>().Database.BeginTransactionAsync())
            {
                await UnitWork.BatchDeleteAsync(waitDeleteSharings.ToArray());

                var addDtos = waitAddSharings.Select(x => new FinanceSaleBonusShare
                {
                    SaleOrder = financeSaleBonusSharingDto.SaleOrder.Value,
                    ShareUserId = x.ShareUserId,
                    ShareUserName = x.ShareUserName,
                    ShareBonusPercent = x.ShareBonusPercent,
                    CreateTime = DateTime.Now,
                    UpdateTime = DateTime.Now,
                    CreateUserId = user.Id,
                    Remark = financeSaleBonusSharingDto.Remark,
                    Status = 0
                }).ToList();
                await UnitWork.BatchAddAsync<FinanceSaleBonusShare, int>(addDtos.ToArray());
                waitUpdateSharings.ForEach(waitUpdateSharing =>
                {
                    var newSharing = newSharings.FirstOrDefault(x => x.ShareUserId == waitUpdateSharing.ShareUserId);
                    waitUpdateSharing.ShareBonusPercent = newSharing.ShareBonusPercent;
                    waitUpdateSharing.UpdateTime = DateTime.Now;
                });
                await UnitWork.BatchUpdateAsync(waitUpdateSharings.ToArray());
                await UnitWork.SaveAsync();
                await transaction.CommitAsync();
            }
        }
        /// <summary>
        /// 检查新增或修改的提成分成信息
        /// </summary>
        /// <param name="financeSaleBonusSharingDto"></param>
        /// <exception cref="NotImplementedException"></exception>
        private async Task CheckSaleBonusSharing(FinanceSaleBonusSharingDto financeSaleBonusSharingDto)
        {
            if (financeSaleBonusSharingDto == null)
            {
                throw new NotImplementedException("分成者参数不能为空");
            }
            if (financeSaleBonusSharingDto.SaleOrder == null)
            {
                throw new NotImplementedException("销售单号不能为空");
            }
            if (financeSaleBonusSharingDto.Sharers == null || financeSaleBonusSharingDto.Sharers.Count <= 0)
            {
                throw new NotImplementedException("分成者不能为空");
            }
            if (financeSaleBonusSharingDto.Sharers.Sum(x => x.ShareBonusPercent) != 100m)
            {
                throw new NotImplementedException("分成比例之和必须为100%");
            }
            if (await UnitWork.Find<ORDR>(null).AnyAsync(x => x.DocEntry == financeSaleBonusSharingDto.SaleOrder.Value))
            {
                throw new NotImplementedException("销售单号不存在");
            }
        }
        /// <summary>
        /// 获取提成分配信息
        /// </summary>
        /// <param name="docEntry"></param>
        /// <param name="slpCode"></param>
        /// <param name="saleUserId"></param>
        /// <returns></returns>
        public async Task<List<SaleBonusShareConfigDto>> GetBonusShareConfig(int? docEntry, int? slpCode, string saleUserId = null)
        {
            List<FinanceSaleBonusShare> selfDefineConfigs = null;
            List<SaleBonusShareConfigDto> result = new List<SaleBonusShareConfigDto>();
            if (!docEntry.HasValue && !slpCode.HasValue && string.IsNullOrEmpty(saleUserId)) return result;
            var systemUsers = await _financeCommonApp.GetSystemUserInfos();
            if (docEntry.HasValue)
            {
                selfDefineConfigs = await UnitWork.Find<FinanceSaleBonusShare>(null).Where(x => x.SaleOrder == docEntry).ToListAsync();
            }
            //若是该订单特别配置了提成配置
            if (selfDefineConfigs != null && selfDefineConfigs.Count > 0)
            {
                result = selfDefineConfigs.Select(x =>
                {
                    return new SaleBonusShareConfigDto
                    {
                        ShareUserId = x.ShareUserId,
                        ShareUserName = x.ShareUserName,
                        SharePercent = x.ShareBonusPercent
                    };
                }).ToList();
            }
            //获取配置
            else
            {

                var nameOrgDic = _userDepartMsgHelp.GetUserNameOrgDictionary();
                var bonusConfig = await UnitWork.Find<FinanceBonusLevel>(null)
                    .WhereIf(!string.IsNullOrEmpty(saleUserId), x => x.UserId == saleUserId)
                    .WhereIf(slpCode.HasValue, x => x.SlpCode == slpCode.Value)
                    .FirstOrDefaultAsync();
                if (bonusConfig != null)
                {
                    var slpCodes = new List<int> { bonusConfig.SlpCode.Value };
                    var sharePercentList = new List<decimal> { bonusConfig.SelfBonusPcnt.Value };
                    if (bonusConfig.FirstLeaderSlpCode.HasValue)
                    {
                        slpCodes.Add(bonusConfig.FirstLeaderSlpCode.Value);
                        sharePercentList.Add(bonusConfig.FirstLeaderBonusPcnt.Value);

                    }
                    if (!string.IsNullOrEmpty(bonusConfig.UpperSlpCode))
                    {
                        slpCodes.AddRange(bonusConfig.UpperSlpCode.Split(',').Select(x => int.Parse(x)).ToList());

                        sharePercentList.AddRange(bonusConfig.UpperLeaderBonusPcnt.Split(',').Select(x => decimal.Parse(x)).ToList());
                    }

                    result = slpCodes.Select((x, index) =>
                    {
                        var user = systemUsers.FirstOrDefault(y => y.SlpCode == x);
                        nameOrgDic.TryGetValue(user.SlpName, out var orgName);
                        return new SaleBonusShareConfigDto
                        {
                            ShareUserId = user.ERP4Id,
                            ShareUserName = string.IsNullOrEmpty(orgName) ? user.SlpName : orgName + "-" + user.SlpName,
                            SharePercent = index == 0 ? sharePercentList[index] : sharePercentList[index] * (100 - bonusConfig.SelfBonusPcnt.Value) / 100 // 使用相同的索引获取对应百分比
                        };
                    }).ToList();

                }
                else
                {
                    var user = systemUsers.FirstOrDefault(x => x.SlpCode == slpCode || x.ERP4Id == saleUserId);
                    nameOrgDic.TryGetValue(user.SlpName, out var orgName);
                    result = new List<SaleBonusShareConfigDto>
                    {
                        new SaleBonusShareConfigDto
                        {
                            ShareUserId = user.ERP4Id,
                            ShareUserName = string.IsNullOrEmpty(orgName) ? user.SlpName : orgName + "-" + user.SlpName,
                            SharePercent = 100m
                        }
                    };
                }
            }
            return result;
        }
        #endregion

        #region 提成导出
        /// <summary>
        /// 
        /// </summary>
        /// <param name="exportTime"></param>
        /// <returns></returns>
        /// <exception cref="NotImplementedException"></exception>
        public async Task<MemoryStream> SaleBonusOrdersSettleDownAndExport(DateTime? exportTime)
        {
            if (!await HasTotalBonusExportPermissions())
            {
                throw new NotImplementedException("您没有提成总表导出权限");
            }
            var waitExportBonusInfos = await UnitWork.Find<FinanceBonusApplication>(null)
                .Where(x => x.BonusStatus == BonusApplicationStatus.已批准 && string.IsNullOrEmpty(x.SettledBatchNo))
                .ToListAsync();
            if (waitExportBonusInfos.Count <= 0)
            {
                return await ExportTotalSaleBonus(null);
            }
            //若有未结算的提成申请，则先结算，再导出
            else
            {
                await SettleDownSaleBonus(waitExportBonusInfos);
                return await ExportTotalSaleBonus(null);
            }

        }
        /// <summary>
        /// 
        /// </summary>
        /// <param name="exportDateTime"></param>
        /// <returns></returns>
        public async Task<MemoryStream> ExportTotalSaleBonus(DateTime? exportDateTime)
        {
            var settledBonusInfos = await UnitWork.Find<FinanceBonusApplication>(null)
                .Where(x => x.SettlementDate != null && x.BonusStatus == BonusApplicationStatus.已结算 && !string.IsNullOrEmpty(x.SettledBatchNo))
                .ToListAsync();
            var currentTime = DateTime.Now;
            if (exportDateTime.HasValue) currentTime = exportDateTime.Value;
            int currentQuarter = (currentTime.Month - 1) / 3 + 1;
            var waitExportBonusInfos = settledBonusInfos.Where(x => x.SettlementDate.Value.Year == currentTime.Year && currentQuarter == ((x.SettlementDate.Value.Month - 1) / 3 + 1)).ToList();

            var applicationIds = waitExportBonusInfos.Select(x => x.Id).ToList();
            var bonusShareDetails = await UnitWork.Find<FinanceSaleBonusShareDetail>(null).Where(x => applicationIds.Contains(x.SaleBonusApplicationId.Value)).ToListAsync();
            var users = await _financeCommonApp.GetSystemUserInfos();
            var saleBonusSummaryDtos = GetSaleBonusSummaryDtos(waitExportBonusInfos, bonusShareDetails, users);
            var saleBonusBatchDetailDtos = GetSaleBonusBatchDetailDtos(waitExportBonusInfos, bonusShareDetails, users);
            var accessoryFeeDtos = GetAccessoryFeeDtos(waitExportBonusInfos.Where(x => x.AfterTaxAccessoryTotalAmount > 0).ToList(), users);

            var exportConfigs = new List<ExcelExportConfig>
            {
                new ExcelExportConfig
                {
                    Title = "季度售后提成明细",
                    DataTypeName = "SaleBonusSummaryDto",
                    SheetName = "提成汇总",
                    Properties = new List<string> {
                        "OrgName", "SaleMan", "LastQuantityRestAmount",
                        "SelfBonusAmount", "AnotherProvideAmount", "DeductSalaryAmount",
                        "DeductOtherAmount", "ShouldPayAmount", "Remark"
                    },
                    SummaryRowDic = new Dictionary<string, string>{
                        { "SelfBonusAmount" , saleBonusSummaryDtos.Sum(x => x.SelfBonusAmount) .ToString("N2")} ,
                        { "AnotherProvideAmount" , saleBonusSummaryDtos.Sum(x => x.AnotherProvideAmount) .ToString("N2")} ,
                    }
                },
                new ExcelExportConfig
                {
                    DataTypeName = "SaleBonusBatchDetailDto",
                    SheetName = "提成批次明细",
                    Properties = new List<string> {
                        "BonusBatchNo", "SaleMan", "SelfBonusAmount", "Status",
                        "FirstLeaderName", "FirstLeaderAmount", "UpperLeaderName",
                        "UpperLeaderAmount", "TotalBonusAmount"
                    }
                },
                new ExcelExportConfig
                {
                    DataTypeName = "AccessoryFeeDto",
                    SheetName = "配件费总表",
                    Properties = new List<string> {
                        "Index", "OrgName", "Name",
                        "Amount", "Percent"
                    },
                    SummaryRowDic = new Dictionary<string, string>{
                        { "Amount" , accessoryFeeDtos.Sum(x => x.Amount) .ToString("N2")} ,
                        { "Percent" , accessoryFeeDtos.Count > 0 ? (accessoryFeeDtos.Sum(x => x.Percent) / accessoryFeeDtos.Count).ToString("N2") : "0.00"} ,
                    }
                }
            };

            return _financeCommonApp.ExportToExcel(
                saleBonusSummaryDtos.Concat(saleBonusBatchDetailDtos.Cast<object>()).Concat(accessoryFeeDtos.Cast<object>()).ToList(),
                exportConfigs
            );
        }
        /// <summary>
        /// 获取总表导出-提成汇总
        /// </summary>
        /// <param name="waitExportBonusInfos"></param>
        /// <param name="bonusShareDetails"></param>
        /// <param name="users"></param>
        /// <returns></returns>
        private List<SaleBonusSummaryDto> GetSaleBonusSummaryDtos(List<FinanceBonusApplication> waitExportBonusInfos, List<FinanceSaleBonusShareDetail> bonusShareDetails, List<SystemUserInfo> users)
        {
            var nameOrgDic = _userDepartMsgHelp.GetUserNameOrgDictionary();

            List<SaleBonusSummaryDto> saleBonusSummaryDtos = new List<SaleBonusSummaryDto>();
            waitExportBonusInfos.GroupBy(x => x.ApplyUserId).ForEach(group =>
            {
                var user = users.FirstOrDefault(x => group.First().ApplyUserId == x.ERP4Id);
                nameOrgDic.TryGetValue(user.SlpName, out var orgName);
                var applicationIds = group.Select(x => x.Id).ToList();
                var applyUserDetails = bonusShareDetails.Where(x => applicationIds.Contains(x.SaleBonusApplicationId.Value) && group.Key == x.UserId).ToList();
                saleBonusSummaryDtos.Add(new SaleBonusSummaryDto
                {
                    UserId = user.ERP4Id,
                    OrgName = orgName,
                    SaleMan = user.SlpName,
                    SelfBonusAmount = applyUserDetails.Sum(x => x.SelfBonusAmount ?? 0),

                });
            });
            bonusShareDetails.ForEach(bonusShareDetail =>
            {
                if (bonusShareDetail.ParentSlpCode.HasValue)
                {
                    var user = users.FirstOrDefault(x => x.SlpCode == bonusShareDetail.ParentSlpCode);
                    var saleBonusSummaryDto = saleBonusSummaryDtos.FirstOrDefault(x => x.UserId == user.ERP4Id);
                    if (saleBonusSummaryDto == null)
                    {
                        nameOrgDic.TryGetValue(user.SlpName, out var orgName);
                        saleBonusSummaryDtos.Add(new SaleBonusSummaryDto
                        {
                            UserId = user.ERP4Id,
                            OrgName = orgName,
                            SaleMan = user.SlpName,
                            AnotherProvideAmount = bonusShareDetail.ParentBonusAmount ?? 0,

                        });
                    }
                    else
                    {
                        saleBonusSummaryDto.AnotherProvideAmount += bonusShareDetail.ParentBonusAmount ?? 0;
                    }
                }
                if (!string.IsNullOrEmpty(bonusShareDetail.UpperSlpCode))
                {
                    var slpCodes = bonusShareDetail.UpperSlpCode.Split(",").Select(x => int.Parse(x)).ToList();
                    var bonusAmountList = bonusShareDetail.UpperBonusAmount.Split(",").Select(x => decimal.Parse(x)).ToList();
                    int count = 0;
                    foreach (var slpCode in slpCodes)
                    {
                        var user = users.FirstOrDefault(x => x.SlpCode == slpCode);
                        var saleBonusSummaryDto = saleBonusSummaryDtos.FirstOrDefault(x => x.UserId == user.ERP4Id);
                        if (saleBonusSummaryDto == null)
                        {
                            nameOrgDic.TryGetValue(user.SlpName, out var orgName);
                            saleBonusSummaryDtos.Add(new SaleBonusSummaryDto
                            {
                                UserId = user.ERP4Id,
                                OrgName = orgName,
                                SaleMan = user.SlpName,
                                AnotherProvideAmount = bonusAmountList[count],

                            });
                        }
                        else
                        {
                            saleBonusSummaryDto.AnotherProvideAmount += bonusAmountList[count];
                        }
                        count++;
                    }
                }
            });
            return saleBonusSummaryDtos;
        }
        /// <summary>
        /// 获取总表导出-提成批次明细
        /// </summary>
        /// <param name="waitExportBonusInfos"></param>
        /// <param name="bonusShareDetails"></param>
        /// <param name="users"></param>
        /// <returns></returns>
        private List<SaleBonusBatchDetailDto> GetSaleBonusBatchDetailDtos(List<FinanceBonusApplication> waitExportBonusInfos, List<FinanceSaleBonusShareDetail> bonusShareDetails, List<SystemUserInfo> users)
        {
            List<SaleBonusBatchDetailDto> saleBonusBatchDetailDtos = new List<SaleBonusBatchDetailDto>();
            waitExportBonusInfos.GroupBy(x => x.SettledBatchNo).ForEach(group =>
            {
                var user = users.FirstOrDefault(x => x.ERP4Id == group.First().ApplyUserId);

                var bonusApplicationIds = group.Select(x => x.Id).ToList();
                var batchBonusShareDetail = bonusShareDetails.Where(x => bonusApplicationIds.Contains(x.SaleBonusApplicationId.Value)).ToList();
                var firstLeader = users.FirstOrDefault(x => x.SlpCode == batchBonusShareDetail[0].ParentSlpCode);
                Dictionary<string, decimal> parentSlpCodeAndBonusAmtDics = new Dictionary<string, decimal>();
                batchBonusShareDetail.ForEach(bonusShareDetail =>
                {
                    if (!string.IsNullOrEmpty(bonusShareDetail.UpperSlpCode))
                    {
                        var upperSlpCodes = bonusShareDetail.UpperSlpCode.Split(',').Select(x => int.Parse(x)).ToList();
                        var upperBonusAmounts = bonusShareDetail.UpperBonusAmount.Split(',').Select(x => decimal.Parse(x)).ToList();
                        for (int i = 0; i < upperSlpCodes.Count; i++)
                        {
                            var upperUser = users.FirstOrDefault(x => x.SlpCode == upperSlpCodes[i]);
                            if (!parentSlpCodeAndBonusAmtDics.ContainsKey(upperUser.SlpName))
                            {
                                parentSlpCodeAndBonusAmtDics.Add(upperUser.SlpName, upperBonusAmounts[i]);
                            }
                            else
                            {
                                parentSlpCodeAndBonusAmtDics[upperUser.SlpName] += upperBonusAmounts[i];
                            }
                        }
                    }
                });
                saleBonusBatchDetailDtos.Add(new SaleBonusBatchDetailDto
                {
                    BonusBatchNo = group.Key,
                    SaleMan = user.SlpName,
                    SelfBonusAmount = batchBonusShareDetail.Sum(x => x.SelfBonusAmount ?? 0),
                    Status = group.First().BonusStatus.ToString(),
                    FirstLeaderName = firstLeader?.SlpName,
                    FirstLeaderAmount = batchBonusShareDetail.Sum(x => x.ParentBonusAmount ?? 0),
                    UpperLeaderName = parentSlpCodeAndBonusAmtDics.Count > 0 ? string.Join(",", parentSlpCodeAndBonusAmtDics.Select(x => x.Key).ToList()) : null,
                    UpperLeaderAmount = parentSlpCodeAndBonusAmtDics.Count > 0 ? string.Join(",", parentSlpCodeAndBonusAmtDics.Select(x => x.Value.ToString()).ToList()) : null,
                    TotalBonusAmount = group.Sum(x => x.BonusTotalAmount)
                });

            });
            return saleBonusBatchDetailDtos;

        }
        /// <summary>
        /// 获取总表导出-配件费总表
        /// </summary>
        /// <param name="waitExportBonusInfos"></param>
        /// <param name="users"></param>
        /// <returns></returns>
        private List<AccessoryFeeDto> GetAccessoryFeeDtos(List<FinanceBonusApplication> waitExportBonusInfos, List<SystemUserInfo> users)
        {
            List<AccessoryFeeDto> accessoryFeeDtos = new List<AccessoryFeeDto>();
            var nameOrgDic = _userDepartMsgHelp.GetUserNameOrgDictionary();
            int index = 1;
            waitExportBonusInfos.GroupBy(x => x.ApplyUserId).ForEach(waitExportBonusInfo =>
            {
                var user = users.FirstOrDefault(x => x.ERP4Id == waitExportBonusInfo.First().ApplyUserId);
                nameOrgDic.TryGetValue(user.SlpName, out var orgName);
                accessoryFeeDtos.Add(new AccessoryFeeDto
                {
                    Index = index++,
                    Name = user.SlpName,
                    OrgName = orgName,
                    Amount = waitExportBonusInfo.Sum(x => x.AfterTaxAccessoryTotalAmount),
                    Percent = waitExportBonusInfo.Sum(x => x.OrderAmount) > 0 ? (waitExportBonusInfo.Sum(x => x.AfterTaxAccessoryTotalAmount) / (waitExportBonusInfo.Sum(x => x.OrderAmount) * 0.8m) * 100m) : 0m
                });
            });
            return accessoryFeeDtos;
        }

        /// <summary>
        /// 提成结算
        /// </summary>
        /// <param name="financeBonusApplications"></param>
        /// <returns></returns>
        private async Task SettleDownSaleBonus(List<FinanceBonusApplication> financeBonusApplications)
        {
            var bonusExportBatchNoSuffix = await GetBonusExportBatchSuffix();
            var currentTime = DateTime.Now;
            financeBonusApplications.GroupBy(x => x.ApplyUserId).ForEach(groupFinanceBonusApplication =>
            {
                var bonusExportBatchNo = $"XSTC{bonusExportBatchNoSuffix}";
                groupFinanceBonusApplication.ToList().ForEach(item =>
                {
                    item.SettledBatchNo = bonusExportBatchNo; // 为组内每条记录赋值
                    item.UpdateTime = currentTime;
                    item.SettlementDate = currentTime;
                    item.BonusStatus = BonusApplicationStatus.已结算;
                });
                bonusExportBatchNoSuffix++;
            }
            );
            var saleOrderNos = financeBonusApplications.Select(x => x.OrderNo).ToList();
            var saleOrderBonusConfigs = await UnitWork.Find<FinanceSaleBonusShare>(null).Where(x => saleOrderNos.Contains(x.SaleOrder)).ToListAsync();
            //若有单独配置分成，状态改为已结算
            saleOrderBonusConfigs.ForEach(x => x.Status = 1);
            List<FinanceSaleBonusShareDetail> financeSaleBonusShareDetails = new List<FinanceSaleBonusShareDetail>();
            //单独配置分成详情生成
            var selfConfigBonusApplications = financeBonusApplications.Where(x => saleOrderBonusConfigs.Any(y => y.SaleOrder == x.OrderNo)).ToList();
            selfConfigBonusApplications.ForEach(bonusApplication =>
            {
                var configs = saleOrderBonusConfigs.Where(x => x.SaleOrder == bonusApplication.OrderNo).ToList();
                configs.ForEach(config =>
                {
                    financeSaleBonusShareDetails.Add(new FinanceSaleBonusShareDetail
                    {
                        SaleOrder = bonusApplication.OrderNo,
                        SaleBonusApplicationId = bonusApplication.Id,
                        UserId = config.ShareUserId,
                        SelfBonusPercent = config.ShareBonusPercent,
                        SelfBonusAmount = bonusApplication.BonusTotalAmount * config.ShareBonusPercent / 100,
                        CreateTime = currentTime
                    });
                });
            });
            //系统配置分成详情生成
            var systemConfigBonusApplications = financeBonusApplications.Where(x => !saleOrderBonusConfigs.Any(y => y.SaleOrder == x.OrderNo)).ToList();
            var saleManIds = systemConfigBonusApplications.Select(x => x.ApplyUserId).Distinct().ToList();
            var bonusConfigs = await UnitWork.Find<FinanceBonusLevel>(null)
                   .Where(x => saleManIds.Contains(x.UserId))
                   .ToListAsync();
            systemConfigBonusApplications.ForEach(bonusApplication =>
            {
                var bonusConfig = bonusConfigs.FirstOrDefault(x => x.UserId == bonusApplication.ApplyUserId);
                financeSaleBonusShareDetails.Add(GenerateFinanceSaleBonusShareDetail(bonusApplication, bonusConfig));
            });
            using (var transaction = await UnitWork.GetDbContext<FinanceBonusApplication>().Database.BeginTransactionAsync())
            {
                await UnitWork.BatchUpdateAsync(financeBonusApplications.ToArray());
                await UnitWork.BatchUpdateAsync(saleOrderBonusConfigs.ToArray());
                await UnitWork.BatchAddAsync<FinanceSaleBonusShareDetail, int>(financeSaleBonusShareDetails.ToArray());
                await UnitWork.SaveAsync();
                await transaction.CommitAsync();
            }
            var waitSyncSapBonsusInfos = new List<(int, int)>();
            financeBonusApplications.ForEach(bonusApplication => waitSyncSapBonsusInfos.Add((bonusApplication.OrderNo.Value, 2)));
            await _capBus.PublishAsync("Serve.BatchUpdateBonusStatus.Add", waitSyncSapBonsusInfos);
        }
        /// <summary>
        /// 生成FinanceSaleBonusShareDetail
        /// </summary>
        /// <param name="bonusApplication"></param>
        /// <param name="bonusConfig"></param>
        /// <returns></returns>
        private static FinanceSaleBonusShareDetail GenerateFinanceSaleBonusShareDetail(FinanceBonusApplication bonusApplication, FinanceBonusLevel bonusConfig)
        {
            var currentTime = DateTime.Now;
            if (bonusConfig != null)
            {
                List<decimal> upperBonusAmount = new List<decimal>();
                if (!string.IsNullOrEmpty(bonusConfig.UpperSlpCode))
                {
                    var upperBonusPercent = bonusConfig.UpperLeaderBonusPcnt.Split(',').Select(x => decimal.Parse(x)).ToList();
                    upperBonusPercent.ForEach(x =>
                    {
                        var upperPercent = (100 - bonusConfig.SelfBonusPcnt.Value) * x / 100m;
                        upperBonusAmount.Add(bonusApplication.BonusTotalAmount * upperPercent / 100m);
                    });
                }
                decimal? parentBonusAmount = null;
                if (bonusConfig.FirstLeaderBonusPcnt.HasValue)
                {
                    var parentPer = (100 - bonusConfig.SelfBonusPcnt.Value) * bonusConfig.FirstLeaderBonusPcnt / 100m;
                    parentBonusAmount = bonusApplication.BonusTotalAmount * parentPer / 100m;
                }
                return new FinanceSaleBonusShareDetail
                {
                    SaleOrder = bonusApplication.OrderNo,
                    SaleBonusApplicationId = bonusApplication.Id,
                    UserId = bonusApplication.ApplyUserId,
                    SelfBonusPercent = bonusConfig.SelfBonusPcnt,
                    SelfBonusAmount = bonusApplication.BonusTotalAmount * bonusConfig.SelfBonusPcnt / 100,
                    CreateTime = currentTime,
                    ParentBonusPercent = bonusConfig.FirstLeaderBonusPcnt,
                    ParentBonusAmount = parentBonusAmount,
                    ParentSlpCode = bonusConfig.FirstLeaderSlpCode,
                    UpperSlpCode = bonusConfig.UpperSlpCode,
                    UpperBonusPercent = bonusConfig.UpperLeaderBonusPcnt,
                    UpperBonusAmount = string.Join(",", upperBonusAmount)
                };
            }
            else
            {
                return new FinanceSaleBonusShareDetail
                {
                    SaleOrder = bonusApplication.OrderNo,
                    SaleBonusApplicationId = bonusApplication.Id,
                    UserId = bonusApplication.ApplyUserId,
                    SelfBonusPercent = 100m,
                    SelfBonusAmount = bonusApplication.BonusTotalAmount,
                    CreateTime = currentTime
                };
            }
        }
        /// <summary>
        /// 
        /// </summary>
        /// <returns></returns>
        /// <exception cref="NotImplementedException"></exception>
        private async Task<int> GetBonusExportBatchSuffix()
        {
            var nowTime = DateTime.Now;
            int currentQuarter = (nowTime.Month - 1) / 3 + 1;
            var settledInfo = await UnitWork.Find<FinanceBonusApplication>(null)
                .Where(x => x.SettlementDate != null && x.BonusStatus == BonusApplicationStatus.已结算 && !string.IsNullOrEmpty(x.SettledBatchNo) && x.SettlementDate.Value.Year == nowTime.Year)
                .OrderByDescending(x => x.SettledBatchNo)
                .FirstOrDefaultAsync();
            var lastSettledQuarter = settledInfo == null ? 0 : (settledInfo.SettlementDate.Value.Month - 1) / 3 + 1;
            if (currentQuarter == lastSettledQuarter)
            {
                // 提取纯数字部分（去掉XSTC前缀）
                var numberPart = settledInfo.SettledBatchNo.Substring(4);
                // 当季度相同时，返回最新批次号+1
                if (int.TryParse(numberPart, out int lastBatchNo))
                {
                    return (lastBatchNo + 1);
                }
                else
                {
                    throw new NotImplementedException("生成导出批次号异常");
                }
            }
            else
            {
                // 新季度返回初始批次号
                return int.Parse(($"{nowTime.Year}{currentQuarter}0001"));
            }
        }
        /// <summary>
        /// 私有方法 - 是否有有提成总表导出权限
        /// </summary>
        /// <returns></returns>
        private async Task<bool> HasTotalBonusExportPermissions()
        {
            var userRoleIds = _auth.GetCurrentUser().Roles.Select(x => x.Id).ToList();
            var moduleElements = await _financeCommonApp.GetModuleElementByRoleIds(userRoleIds, moduleCode);
            if (moduleElements.Exists(x => x.DomId == totalSaleBonusExport))
                return true;
            return false;
        }

        /// <summary>
        /// 
        /// </summary>
        /// <param name="exportDateTime"></param>
        /// <returns></returns>
        public async Task<MemoryStream> PersonalSaleBonusExport(DateTime? exportDateTime = null)
        {
            var user = _auth.GetCurrentUser().User;
            var settledBonusInfos = await UnitWork.Find<FinanceBonusApplication>(null)
                .Where(x => x.CreateTime != null
                       && (x.BonusStatus == BonusApplicationStatus.草稿 || x.BonusStatus == BonusApplicationStatus.已结算 || x.BonusStatus == BonusApplicationStatus.审批中 || x.BonusStatus == BonusApplicationStatus.已批准)
                       //&& !string.IsNullOrEmpty(x.SettledBatchNo)
                       && x.ApplyUserId == user.Id)
                .ToListAsync();

            var currentTime = exportDateTime ?? DateTime.Now;
            int currentQuarter = (currentTime.Month - 1) / 3 + 1;

            var waitExportBonusInfos = settledBonusInfos
                .Where(x => x.CreateTime.Value.Year == currentTime.Year
                       && currentQuarter == ((x.CreateTime.Value.Month - 1) / 3 + 1))
                .ToList();
            var applicationIds = waitExportBonusInfos.Select(x => x.Id).ToList();
            var bonusShareDetails = await UnitWork.Find<FinanceSaleBonusShareDetail>(null)
                .Where(x => x.UserId == user.Id && applicationIds.Contains(x.SaleBonusApplicationId.Value))
                .ToListAsync();
            //未结算的，生成临时分配数据
            var bonusConfigLevel = await UnitWork.Find<FinanceBonusLevel>(null).FirstOrDefaultAsync(x => x.UserId == user.Id);
            var orderNos = settledBonusInfos.Where(x => x.BonusStatus != BonusApplicationStatus.已结算).Select(x => x.OrderNo.Value).ToList();
            var saleOrderBonusConfigs = await UnitWork.Find<FinanceSaleBonusShare>(null).Where(x => x.ShareUserId == user.Id && orderNos.Contains(x.SaleOrder)).ToListAsync();
            settledBonusInfos.Where(x => x.BonusStatus != BonusApplicationStatus.已结算).ForEach(bonusApplication =>
            {
                var saleOrderBonusConfig = saleOrderBonusConfigs.FirstOrDefault(x => bonusApplication.OrderNo.Value == x.SaleOrder);
                if (saleOrderBonusConfig != null)
                {
                    bonusShareDetails.Add(new FinanceSaleBonusShareDetail
                    {
                        SaleOrder = bonusApplication.OrderNo,
                        SaleBonusApplicationId = bonusApplication.Id,
                        UserId = saleOrderBonusConfig.ShareUserId,
                        SelfBonusPercent = saleOrderBonusConfig.ShareBonusPercent,
                        SelfBonusAmount = bonusApplication.BonusTotalAmount * saleOrderBonusConfig.ShareBonusPercent / 100,
                    });
                }
                else
                {
                    bonusShareDetails.Add(GenerateFinanceSaleBonusShareDetail(bonusApplication, bonusConfigLevel));
                }
            });
            var bonusDetails = await UnitWork.Find<FinanceSaleBonusDetails>(null)
                .Where(x => applicationIds.Contains(x.FinanceBonusId.Value))
                .ToListAsync();

            var users = await _financeCommonApp.GetSystemUserInfos();

            // 生成个人提成明细数据
            var personalDetails = GetPersonalSaleBonusDetailDtos(waitExportBonusInfos, bonusDetails, bonusShareDetails, users);
            var nameOrgDic = _userDepartMsgHelp.GetUserNameOrgDictionary();
            nameOrgDic.TryGetValue(user.Name, out var orgName);
            // 配置Excel导出
            var exportConfig = new ExcelExportConfig
            {
                Title = $"售后提成统计报表_{orgName ?? ""}{user.Name}",
                SubTitle = $"{string.Join(",", waitExportBonusInfos.Select(x => x.SettledBatchNo).Distinct())}",
                DataTypeName = "PersonalSaleBonusDetailDto",
                SheetName = "提成明细",
                Properties = new List<string> {
                    "Index", "ApprovalNumber", "CustomerCode",
                    "OrderNumber", "MaterialCode", "Quantity",
                    "UnitPrice", "TotalAmount", "PreTaxAccessoryFee",
                    "AfterTaxAccessoryFee", "TotalDeduction",
                    "SalesCommissionRate","BonusAmount","SelfBonusAmount"
                },
                DisplayName = new Dictionary<string, string> { { "SelfBonusAmount", $"{bonusConfigLevel?.SelfBonusPcnt.Value.ToString("0.00") ?? "100.00"}%({user.Name})" } }
            };

            return PersonalSaleBonusExportToExcel(personalDetails, exportConfig);
        }
        /// <summary>
        /// 
        /// </summary>
        /// <param name="bonusApplications"></param>
        /// <param name="bonusDetails"></param>
        /// <param name="bonusShareDetails"></param>
        /// <param name="users"></param>
        /// <returns></returns>
        private List<PersonalSaleBonusDetailDto> GetPersonalSaleBonusDetailDtos(
            List<FinanceBonusApplication> bonusApplications,
            List<FinanceSaleBonusDetails> bonusDetails,
            List<FinanceSaleBonusShareDetail> bonusShareDetails,
            List<SystemUserInfo> users)
        {
            var result = new List<PersonalSaleBonusDetailDto>();

            int index = 1;
            foreach (var app in bonusApplications)
            {
                var details = bonusDetails.Where(x => x.FinanceBonusId == app.Id).ToList();
                var shareDetail = bonusShareDetails.FirstOrDefault(x => x.SaleBonusApplicationId == app.Id);

                foreach (var detail in details)
                {
                    var dto = new PersonalSaleBonusDetailDto
                    {
                        Index = index.ToString(),
                        CustomerCode = app.CardCode,
                        OrderNumber = app.OrderNo.Value.ToString(),
                        MaterialCode = detail.SaleItemCode,
                        Quantity = detail.ItemCount.ToString("N2"),
                        UnitPrice = detail.UnitPrice,
                        TotalAmount = detail.TotalAmount,
                        AfterTaxAccessoryFee = detail.AfterTaxAccessoryAmount,
                        TotalDeduction = detail.TotalDeductAmount,
                        SalesCommissionRate = detail.BonusPercent,
                        BonusAmount = detail.BonusAmount,
                        SelfBonusAmount = detail.BonusAmount * shareDetail.SelfBonusPercent.Value / 100,
                    };

                    // 添加领导信息
                    if (shareDetail != null)
                    {
                        if (shareDetail.ParentSlpCode.HasValue)
                        {
                            var leader = users.FirstOrDefault(x => x.SlpCode == shareDetail.ParentSlpCode);

                            if (leader != null)
                            {
                                var percent = (100m - shareDetail.SelfBonusPercent.Value) * shareDetail.ParentBonusPercent / 100;
                                var parentBonusAmount = percent * detail.BonusAmount / 100;
                                var columnName = $"{percent.Value.ToString("N2")}%({leader.SlpName})";
                                dto.DynamicLeadersAndAmount.Add(columnName, (parentBonusAmount ?? 0));
                            }
                        }
                        if (!string.IsNullOrEmpty(shareDetail.UpperSlpCode))
                        {
                            var upperSlpCodes = shareDetail.UpperSlpCode.Split(',').Select(int.Parse).ToList();
                            var upperBonusPercent = shareDetail.UpperBonusPercent.Split(',').Select(decimal.Parse).ToList();
                            for (int i = 0; i < upperSlpCodes.Count; i++)
                            {
                                var upperLeader = users.FirstOrDefault(x => x.SlpCode == upperSlpCodes[i]);
                                if (upperLeader != null)
                                {
                                    var percent = ((100m - shareDetail.SelfBonusPercent.Value) * upperBonusPercent[i]) / 100;
                                    var upperBonusAmount = percent * detail.BonusAmount / 100;
                                    var columnName = $"{percent.ToString("N2")}%({upperLeader.SlpName})";
                                    dto.DynamicLeadersAndAmount.Add(columnName, upperBonusAmount);
                                }
                            }
                        }
                    }

                    result.Add(dto);
                }

                index++;
            }
            return result;
        }
        /// <summary>
        /// 个人提成excel
        /// </summary>
        /// <param name="exportDataList"></param>
        /// <param name="sheetConfig"></param>
        /// <returns></returns>
        private static MemoryStream PersonalSaleBonusExportToExcel(List<PersonalSaleBonusDetailDto> exportDataList, ExcelExportConfig sheetConfig)
        {
            var stream = new MemoryStream();
            using (var package = new ExcelPackage(stream))
            {

                var worksheet = package.Workbook.Worksheets.Add("sheet1");
                List<PropertyInfo> properties = null;
                if (exportDataList.Count > 0)
                {
                    properties = exportDataList[0].GetType().GetProperties()
                    .Where(p => sheetConfig.Properties.Contains(p.Name))
                    .ToList();
                }
                else
                {
                    properties = Assembly.GetExecutingAssembly().GetTypes()
                        .FirstOrDefault(t => t.Name == sheetConfig.DataTypeName).GetProperties()
                    .Where(p => sheetConfig.Properties.Contains(p.Name))
                    .ToList();
                }

                // 添加标题和表头（保持原有逻辑）
                int cellsRowExtraCount = 0;
                var dynamicCol = exportDataList.FirstOrDefault(x => x.DynamicLeadersAndAmount.Count > 0)?.DynamicLeadersAndAmount ?? new Dictionary<string, decimal>();
                if (!string.IsNullOrEmpty(sheetConfig.Title))
                {
                    worksheet.Cells[1, 1].Value = sheetConfig.Title;
                    worksheet.Cells[1, 1, 2, properties.Count + dynamicCol.Count].Merge = true;
                    var titleCell = worksheet.Cells[1, 1];
                    titleCell.Style.HorizontalAlignment = ExcelHorizontalAlignment.Center; // 水平居中
                    titleCell.Style.VerticalAlignment = ExcelVerticalAlignment.Center;    // 垂直居中
                    titleCell.Style.Font.Size = 16;
                    titleCell.Style.Font.Bold = true;
                    cellsRowExtraCount += 2;
                }
                // 添加副标题
                if (!string.IsNullOrEmpty(sheetConfig.SubTitle))
                {
                    int subTitleRow = 2 + (string.IsNullOrEmpty(sheetConfig.Title) ? 0 : 1);
                    worksheet.Cells[subTitleRow, 1].Value = sheetConfig.SubTitle;
                    worksheet.Cells[subTitleRow, 1, subTitleRow, properties.Count + dynamicCol.Count].Merge = true; // 修正结束行号
                    worksheet.Cells[subTitleRow, 1].Style.Font.Size = 12;
                    worksheet.Cells[subTitleRow, 1].Style.Font.Bold = true;
                    worksheet.Cells[subTitleRow, 1].Style.HorizontalAlignment = ExcelHorizontalAlignment.Center;
                    cellsRowExtraCount += 1;
                }

                // 添加表头行
                for (int col = 1; col <= properties.Count; col++)
                {
                    var description = properties[col - 1].GetCustomAttribute<DescriptionAttribute>()?.Description;
                    worksheet.Cells[1 + cellsRowExtraCount, col].Value = description ?? properties[col - 1].Name;
                    if (sheetConfig.DisplayName != null && sheetConfig.DisplayName.ContainsKey(properties[col - 1].Name))
                    {
                        worksheet.Cells[1 + cellsRowExtraCount, col].Value = sheetConfig.DisplayName[properties[col - 1].Name];
                    }
                }

                //添加动态表头行
                var keys = dynamicCol.Select(x => x.Key).ToArray();
                for (int col = 0; col < dynamicCol.Count; col++)
                {
                    worksheet.Cells[1 + cellsRowExtraCount, 1 + col + properties.Count].Value = keys[col];
                }
                // 添加数据行
                int dataStartRow = 2 + cellsRowExtraCount;
                //总动态列合计金额
                var dynamicAmountInfos = new Dictionary<int, decimal>();
                exportDataList.GroupBy(x => x.Index).ForEach(group =>
                {
                    worksheet.Cells[dataStartRow, 1].Value = group.Key;
                    worksheet.Cells[dataStartRow, 1].Style.HorizontalAlignment = ExcelHorizontalAlignment.Center;
                    worksheet.Cells[dataStartRow, 2].Value = group.FirstOrDefault()?.CustomerCode;
                    worksheet.Cells[dataStartRow, 3].Value = group.FirstOrDefault()?.OrderNumber;
                    Dictionary<int, decimal> dynamicTotalAmountInfo = new Dictionary<int, decimal>();
                    group.ForEach(data =>
                    {

                        worksheet.Cells[dataStartRow, 4].Value = data.MaterialCode;
                        worksheet.Cells[dataStartRow, 5].Value = data.Quantity;

                        worksheet.Cells[dataStartRow, 6].Style.Numberformat.Format = NumberFormat;
                        worksheet.Cells[dataStartRow, 6].Value = data.UnitPrice;

                        worksheet.Cells[dataStartRow, 7].Style.Numberformat.Format = NumberFormat;
                        worksheet.Cells[dataStartRow, 7].Value = data.TotalAmount;

                        worksheet.Cells[dataStartRow, 8].Style.Numberformat.Format = NumberFormat;
                        worksheet.Cells[dataStartRow, 8].Value = data.AfterTaxAccessoryFee;

                        worksheet.Cells[dataStartRow, 9].Style.Numberformat.Format = NumberFormat;
                        worksheet.Cells[dataStartRow, 9].Value = data.TotalDeduction;

                        worksheet.Cells[dataStartRow, 10].Style.Numberformat.Format = NumberFormat;
                        worksheet.Cells[dataStartRow, 10].Value = data.SalesCommissionRate;

                        worksheet.Cells[dataStartRow, 11].Style.Numberformat.Format = NumberFormat;
                        worksheet.Cells[dataStartRow, 11].Value = data.BonusAmount;

                        worksheet.Cells[dataStartRow, 12].Style.Numberformat.Format = NumberFormat;
                        worksheet.Cells[dataStartRow, 12].Value = data.SelfBonusAmount;
                        int dynamicColIndex = 13;
                        data.DynamicLeadersAndAmount.ForEach(dynaminData =>
                        {
                            worksheet.Cells[dataStartRow, dynamicColIndex].Style.Numberformat.Format = NumberFormat;
                            worksheet.Cells[dataStartRow, dynamicColIndex].Value = dynaminData.Value;
                            if (dynamicTotalAmountInfo.TryGetValue(dynamicColIndex, out var amount))
                            {
                                dynamicTotalAmountInfo[dynamicColIndex] = amount + dynaminData.Value;
                            }
                            else
                            {
                                dynamicTotalAmountInfo[dynamicColIndex] = dynaminData.Value;
                            }
                            if (dynamicAmountInfos.TryGetValue(dynamicColIndex, out var dynamicAmount))
                            {
                                dynamicAmountInfos[dynamicColIndex] = dynamicAmount + dynaminData.Value;
                            }
                            else
                            {
                                dynamicAmountInfos[dynamicColIndex] = dynaminData.Value;
                            }
                            dynamicColIndex++;
                        });
                        dataStartRow++;
                    });
                    //每单合计

                    worksheet.Cells[dataStartRow, 7].Style.Numberformat.Format = NumberFormat;
                    worksheet.Cells[dataStartRow, 7].Style.Font.Bold = true;
                    worksheet.Cells[dataStartRow, 7].Value = group.Sum(x => x.TotalAmount);

                    worksheet.Cells[dataStartRow, 8].Style.Numberformat.Format = NumberFormat;
                    worksheet.Cells[dataStartRow, 8].Style.Font.Bold = true;
                    worksheet.Cells[dataStartRow, 8].Value = group.Sum(x => x.AfterTaxAccessoryFee);

                    worksheet.Cells[dataStartRow, 9].Style.Numberformat.Format = NumberFormat;
                    worksheet.Cells[dataStartRow, 9].Style.Font.Bold = true;
                    worksheet.Cells[dataStartRow, 9].Value = group.Sum(x => x.TotalDeduction);

                    worksheet.Cells[dataStartRow, 10].Style.Font.Bold = true;
                    worksheet.Cells[dataStartRow, 10].Style.HorizontalAlignment = ExcelHorizontalAlignment.Center;
                    worksheet.Cells[dataStartRow, 10].Value = "小计";

                    worksheet.Cells[dataStartRow, 11].Style.Numberformat.Format = NumberFormat;
                    worksheet.Cells[dataStartRow, 11].Style.Font.Bold = true;
                    worksheet.Cells[dataStartRow, 11].Value = group.Sum(x => x.BonusAmount);

                    worksheet.Cells[dataStartRow, 12].Style.Numberformat.Format = NumberFormat;
                    worksheet.Cells[dataStartRow, 12].Style.Font.Bold = true;
                    worksheet.Cells[dataStartRow, 12].Value = group.Sum(x => x.SelfBonusAmount);
                    dynamicTotalAmountInfo.ForEach(totalInfo =>
                    {
                        worksheet.Cells[dataStartRow, totalInfo.Key].Value = totalInfo.Value;
                        worksheet.Cells[dataStartRow, totalInfo.Key].Style.Font.Bold = true;
                    });
                    dataStartRow++;

                });
                //添加总合计行
                worksheet.Cells[dataStartRow, 6].Style.Font.Bold = true;
                worksheet.Cells[dataStartRow, 6].Style.HorizontalAlignment = ExcelHorizontalAlignment.Center;
                worksheet.Cells[dataStartRow, 6].Value = "合计";

                worksheet.Cells[dataStartRow, 7].Style.Numberformat.Format = NumberFormat;
                worksheet.Cells[dataStartRow, 7].Style.Font.Bold = true;
                worksheet.Cells[dataStartRow, 7].Value = exportDataList.Sum(x => x.TotalAmount);


                worksheet.Cells[dataStartRow, 8].Style.Numberformat.Format = NumberFormat;
                worksheet.Cells[dataStartRow, 8].Style.Font.Bold = true;
                worksheet.Cells[dataStartRow, 8].Value = exportDataList.Sum(x => x.AfterTaxAccessoryFee);

                worksheet.Cells[dataStartRow, 9].Style.Numberformat.Format = NumberFormat;
                worksheet.Cells[dataStartRow, 9].Style.Font.Bold = true;
                worksheet.Cells[dataStartRow, 9].Value = exportDataList.Sum(x => x.TotalDeduction);


                worksheet.Cells[dataStartRow, 11].Style.Numberformat.Format = NumberFormat;
                worksheet.Cells[dataStartRow, 11].Style.Font.Bold = true;
                worksheet.Cells[dataStartRow, 11].Value = exportDataList.Sum(x => x.BonusAmount);

                worksheet.Cells[dataStartRow, 12].Style.Numberformat.Format = NumberFormat;
                worksheet.Cells[dataStartRow, 12].Style.Font.Bold = true;
                worksheet.Cells[dataStartRow, 12].Value = exportDataList.Sum(x => x.SelfBonusAmount);
                dynamicAmountInfos.ForEach(totalInfo =>
                {
                    worksheet.Cells[dataStartRow, totalInfo.Key].Style.Font.Bold = true;
                    worksheet.Cells[dataStartRow, totalInfo.Key].Value = totalInfo.Value;
                });
                dataStartRow++;
                // 应用样式（保持原有逻辑）
                worksheet.Cells.AutoFitColumns();


                package.Save();
            }
            stream.Position = 0;
            return stream;
        }
        #endregion
        /// <summary>
        /// 更新提成信息到SAP
        /// </summary>
        /// <param name="values"></param>
        /// <returns></returns>
        /// <exception cref="InvalidOperationException"></exception>
        private async Task UpdateBonusInfoToOrdrStat(List<(int, int)> values)
        {
            var req = values.Select(x => new UpdateSaleOrderBonusReq()
            {
                DocEntry = x.Item1,
                BonusStatus = x.Item2,
            }).ToList();
            var token = await _httpClientHelper.GetIDCToken();
            string url = $"{_configuration.GetValue<string>(Define.IDCApi)}/api/Handler/UpdateBonusStatusToOrdrState";
            var result = await _httpClientHelper.Post<TableData>(url, req, token);
            if (result == null || result.Code != 200)
            {
                throw new InvalidOperationException($"提成结算信息同步SAP失败：{result?.Message ?? "IDC响应结果为空"}");
            }
        }

        /// <summary>
        /// 
        /// </summary>
        /// <param name="docEntry"></param>
        /// <returns></returns>
        public async Task<TableData<FinanceAfterSaleBonusDetailResp>> GetSaleBonusApplicationBaseInfo(int? docEntry)
        {

            var result = new TableData<FinanceAfterSaleBonusDetailResp> { Data = new FinanceAfterSaleBonusDetailResp() };
            if (!docEntry.HasValue)
                return ConstructErrorResponse(result, "请选择销售订单");
            if (await UnitWork.Find<FinanceBonusApplication>(null).AnyAsync(x => x.OrderNo == docEntry))
                return ConstructErrorResponse(result, $"该订单({docEntry})售后提成已提交或保存草稿，请勿重复申请");

            result.Data.saleBonusHeaderDto = await GetSaleBonusDetailHeader(docEntry.Value);
            result.Data.saleBonusItemDetailDtos = await GetSaleBonusDetailItems(docEntry.Value);

            return result;
        }

        /// <summary>
        /// 获取待申请的售后提成详情页头
        /// </summary>
        /// <param name="docEntry"></param>
        /// <returns></returns>
        private async Task<SaleBonusHeaderDto> GetSaleBonusDetailHeader(int docEntry)
        {
            var ordrInfos = await (from a in UnitWork.Find<ORDR>(null)
                                   join b in UnitWork.Find<OrdrStat>(null) on a.DocEntry.ToString() equals b.Code
                                   join c in UnitWork.Find<OIDC>(null) on a.Indicator equals c.Code
                                   join d in UnitWork.Find<OSLP>(null) on a.SlpCode equals d.SlpCode
                                   where a.DocEntry == docEntry
                                   select new SaleBonusHeaderDto
                                   {
                                       DocEntry = a.DocEntry,
                                       CardCode = a.CardCode,
                                       CardName = a.CardName,
                                       OrderAmount = a.DocTotal,
                                       DeliveryAmount = (b.U_Delivery_TotalAmt ?? 0) - (b.U_Credit_TotalAmt ?? 0),
                                       ReceiptAmount = (a.U_DocRCTAmount ?? 0) - b.U_Refund_Amt,
                                       Indicator = c.Code,
                                       IndicatorName = c.Name,
                                       SlpName = d.SlpName,
                                       SlpCode = d.SlpCode
                                   }).FirstOrDefaultAsync();
            if (ordrInfos == null) return new SaleBonusHeaderDto();
            var adjustAmount = await UnitWork.Find<FinanceBonusFee>(null).Where(x => x.OrderNo == docEntry).SumAsync(x => x.FeeAmount);
            var nameOrgDic = _userDepartMsgHelp.GetUserNameOrgDictionary();
            nameOrgDic.TryGetValue(ordrInfos.SlpName, out var orgName);
            ordrInfos.SlpName = string.IsNullOrEmpty(orgName) ? ordrInfos.SlpName : orgName + "-" + ordrInfos.SlpName;
            ordrInfos.AdjustAmount = adjustAmount;
            return ordrInfos;
        }

        /// <summary>
        /// 
        /// </summary>
        /// <typeparam name="T"></typeparam>
        /// <param name="tableData"></param>
        /// <param name="message"></param>
        /// <returns></returns>
        private static TableData<T> ConstructErrorResponse<T>(TableData<T> tableData, string message)
        {
            tableData.Code = 500;
            tableData.Message = message;
            return tableData;
        }

        /// <summary>
        /// 获取售后提成申请详情
        /// </summary>
        /// <param name="docEntry"></param>
        /// <returns></returns>
        public async Task<TableData<FinanceAfterSaleBonusDetailResp>> GetSaleBonusApplicationDetail(int? docEntry)
        {
            var result = new TableData<FinanceAfterSaleBonusDetailResp>();
            if (!docEntry.HasValue)
                return ConstructErrorResponse(result, "请选择订单号");
            var bonusApplicationInfo = await UnitWork.Find<FinanceBonusApplication>(null)
                                        .Include(x => x.financeSaleBonusDetails)
                                            .ThenInclude(x => x.financeSaleBonusDeductDetails)
                                        .Include(x => x.financeSaleBonusOperationHis)
                                        .Where(x => x.OrderNo == docEntry && x.BonusType == Repository.Domain.Settlement.BonusType.售后提成)
                                        .FirstOrDefaultAsync();
            if (bonusApplicationInfo == null)
                return ConstructErrorResponse(result, $"该订单({docEntry})售后提成未提交或保存草稿");
            result.Data = await ConvertToSaleBonusApplicationDetail(bonusApplicationInfo);
            return result;
        }

        /// <summary>
        /// 数据库实体转换成页面详情
        /// </summary>
        /// <param name="bonusInfo"></param>
        /// <returns></returns>
        private async Task<FinanceAfterSaleBonusDetailResp> ConvertToSaleBonusApplicationDetail(FinanceBonusApplication bonusInfo)
        {
            var result = new FinanceAfterSaleBonusDetailResp();
            var currentUser = _auth.GetCurrentUser();
            var oidcDic = await UnitWork.Find<OIDC>(null).ToDictionaryAsync(x => x.Code, x => x.Name);
            var nameOrgDic = _userDepartMsgHelp.GetUserNameOrgDictionary();
            var user = await UnitWork.Find<User>(null).Where(x => x.Id == bonusInfo.ApplyUserId).FirstOrDefaultAsync();
            var deductionCollections = await UnitWork.Find<FinanceDeductionCollection>(null).ToListAsync();
            nameOrgDic.TryGetValue(user.Name, out var orgName);
            var saleMan = string.IsNullOrEmpty(orgName) ? user.Name ?? "" : orgName + "-" + (user.Name ?? "");
            result.IdStr = $"{OrderType.AB}-{bonusInfo.Id}";
            result.saleBonusHeaderDto = new SaleBonusHeaderDto
            {
                Indicator = bonusInfo.Indicator,
                IndicatorName = string.IsNullOrEmpty(bonusInfo.Indicator) ? "" : oidcDic[bonusInfo.Indicator],
                CardCode = bonusInfo.CardCode,
                CardName = bonusInfo.CardName,
                DocEntry = bonusInfo.OrderNo,
                OrderAmount = bonusInfo.OrderAmount,
                DeliveryAmount = bonusInfo.DeliveryTotalAmount,
                ReceiptAmount = bonusInfo.ReceiptTotalAmount,
                AdjustAmount = bonusInfo.AdjustTotalAmount,
                SlpName = saleMan,
                SubOrderNo = bonusInfo.SubOrderNo
            };
            result.saleBonusItemDetailDtos = bonusInfo.financeSaleBonusDetails.Select(x => new AfterSaleBonusDetailDto
            {
                Id = x.Id,
                SaleItemCode = x.SaleItemCode,
                ItemDescribtion =x.ItemDescribtion,
                ItemCost = x.StockPrice,
                AfterSalesSetPrc = x.AfterSalePrice,
                AfterSalesSetTotal = x.TotalAmount - x.BonusAmount,
                UnitPrice = x.UnitPrice,
                PurUnitPrice = x.PrcUnitPrice,
                LineTotal = x.TotalAmount,
                RateAmount = 0 ,
                ExchangeRateDiffAmount = x.ExchangeRateDiffAmount,
                ItemCount =x.ItemCount,
                BonusAmount = x.BonusAmount
            }).ToList();

            result.saleBonusItemDetailDtos.ForEach(itemDetail =>
            {
                var itemDeductInfos = deductionCollections.Select(x => new ItemDeductInfo
                {
                    DeductionId = x.Id,
                    DeductAmount = 0,
                    DeductionName = x.Name,
                }).ToList();
                var bonunsDeductDetails = bonusInfo.financeSaleBonusDetails.FirstOrDefault(x => x.Id == itemDetail.Id).financeSaleBonusDeductDetails;
                itemDeductInfos.ForEach(x =>
                {
                    var bonunsDeductDetail = bonunsDeductDetails.FirstOrDefault(y => y.DeductionCollectionId == x.DeductionId);
                    if (bonunsDeductDetail != null)
                    {
                        x.Id = bonunsDeductDetail.Id;
                        x.DeductAmount = bonunsDeductDetail.DeductAmount ?? 0;
                    }
                });
                itemDetail.DeductInfos = itemDeductInfos;
            });

            result.saleBonusApprovalHisDtos = bonusInfo.financeSaleBonusOperationHis.Select(x =>
            {
                nameOrgDic.TryGetValue(x.CreateUser, out var orgName);
                var operationUser = x.CreateUserId == Define.ADMINID || string.IsNullOrEmpty(orgName) ? x.CreateUser : orgName + "-" + x.CreateUser;
                return new SaleBonusApprovalHisDto
                {
                    Content = x.Action,
                    CreateUserName = operationUser,
                    CreateUserId = x.CreateUserId,
                    CreateDate = x.CreateTime,
                    ApprovalResult = x.ApprovalResult,
                    IntervalTime = x.IntervalTime,
                    Remark = x.Remark
                };
            }).ToList();
            if (!string.IsNullOrEmpty(bonusInfo.FlowInstanceId))
            {
                var flowInstance = await UnitWork.Find<FlowInstance>(null).Where(x => x.Id == bonusInfo.FlowInstanceId).FirstOrDefaultAsync();
                if (flowInstance != null)
                {
                    if (bonusInfo.BonusStatus == BonusApplicationStatus.审批中)
                    {
                        var approvalUserIds = flowInstance.MakerList.Split(',').ToList();
                        result.CurrentFlowNode = flowInstance.ActivityName;
                        if (approvalUserIds.Contains(currentUser.User?.Id))
                        {
                            result.IsShowFlowNode = true;
                        }
                    }

                    var histories = await UnitWork.Find<FlowInstanceOperationHistory>(fioh => fioh.InstanceId == bonusInfo.FlowInstanceId).ToListAsync();
                    var wf = new WorkflowSerialize(OrderType.AB, flowInstance, currentUser.User, histories);
                    result.ProcessNode = wf.GetProcesses();
                }
            }

            return result;
        }

        /// <summary>
        /// 获取售后提成销售物料信息
        /// </summary>
        /// <param name="docEntry"></param>
        /// <returns></returns>
        private async Task<List<AfterSaleBonusDetailDto>> GetSaleBonusDetailItems(int docEntry)
        {
            var currentUser = _auth.GetCurrentUser()?.User;
            var ordr = await UnitWork.Find<ORDR>(x => x.DocEntry == docEntry).FirstOrDefaultAsync();
            var orgName = _userDepartMsgHelp.GetUserOrgName(currentUser.Id);
            var deliveryInfos = await (from a in UnitWork.Find<DLN1>(null)
                                       join b in UnitWork.Find<ODLN>(null) on a.DocEntry equals b.DocEntry
                                       join c in UnitWork.Find<RDR1>(null) on new { DocEntry = a.BaseEntry, LineNum = a.BaseLine, a.PriceBefDi, a.ItemCode } equals new { c.DocEntry, c.LineNum, c.PriceBefDi, c.ItemCode }
                                       join d in UnitWork.Find<OITM>(null) on c.ItemCode equals d.ItemCode
                                       where docEntry == a.BaseEntry && b.CANCELED == "N"
                                       select
                                       new
                                       {
                                           DeliveryDocEntry = a.DocEntry,
                                           a.ItemCode,
                                           a.Dscription,
                                           a.PriceBefDi,//折扣前价格（不一定为rmb） 
                                           a.BaseLine,
                                           a.BaseEntry,
                                           a.Quantity,
                                           a.LineTotal,//行总计(rmb)
                                           a.Currency,
                                           a.VatPrcnt,//每行税率
                                           a.OpenQty,//剩余未清数量
                                           a.StockPrice,//库存价格（物料成本）
                                           d.LastPurPrc,//最新采购价格（物料成本）
                                       }
                                       ).ToListAsync();

            var poItems = await GetSaleOrdetRelatedPOItems(docEntry);
            var poItemDic = poItems.GroupBy(x => x.ItemCode).ToDictionary(x => x.Key);
            
            var groupedDeliveryInfos = deliveryInfos
                    .GroupBy(x => new { x.ItemCode, x.PriceBefDi, x.BaseLine , })
                    .Select(g => {
                       
                        var poAvgPrice = g.First().StockPrice.Value;
                        if(poItemDic.TryGetValue(g.Key.ItemCode, out var getPoItems))
                        {
                            poAvgPrice = getPoItems.Average(x => x.UnitPrice);
                        }
                       
                      
                        return new AfterSaleBonusDetailDto
                        {
                            SaleItemCode = g.Key.ItemCode,
                            ItemDescribtion = g.First().Dscription,
                            UnitPrice = g.Key.PriceBefDi ?? 0,
                            ItemCount = g.Sum(x => (x.Quantity ?? 0)) ,
                            LineTotal = g.Sum(x => (x.LineTotal ?? 0)),
                            AfterSalesSetPrc = 0,
                            AfterSalesSetTotal = 0,
                            ItemCost = g.First().StockPrice.Value,
                            PurUnitPrice = poAvgPrice,
                            RateAmount = 0,
                            ExchangeRateDiffAmount = 0
                        };
                    }).ToList();

             
            //计算配置扣减金额
            await GenerateItemDeductInfos(docEntry, groupedDeliveryInfos, poItems);
            var strU_sl = string.IsNullOrEmpty(ordr.U_SL) ? "0" : ordr.U_SL;
            var rate = decimal.Parse(strU_sl) / 100m;
            var orderAmt = groupedDeliveryInfos.Sum(x=>x.LineTotal);
            if (ordr.DocCur != FinanceConsts.RMB && groupedDeliveryInfos.Exists(x => x.LineTotal > 0))
            {
                groupedDeliveryInfos.FirstOrDefault(x => x.LineTotal > 0).ExchangeRateDiffAmount = ordr.U_DocRCTAmount / orderAmt;
            }
            foreach (var item in groupedDeliveryInfos)
            {
                decimal rateAmount = item.LineTotal * rate;
                if (orgName == "CS1" || orgName == "CS17" || orgName == "CS35")
                {
                    rateAmount = 0;
                }
                item.RateAmount = rateAmount;
                if (SaleItemCodeBonusRegex.IsMatch(item.SaleItemCode))
                {
                    item.BonusAmount = decimal.Round (( item.LineTotal - rateAmount - item.AfterSalesSetTotal + (item.ExchangeRateDiffAmount ?? 0m)) /2m , 2, MidpointRounding.AwayFromZero);
                }
                else
                {
                    item.BonusAmount = decimal.Round( item.LineTotal - rateAmount - item.AfterSalesSetTotal + (item.ExchangeRateDiffAmount ?? 0m),2, MidpointRounding.AwayFromZero);
                }
            }   
            return groupedDeliveryInfos.Where(x => x.UnitPrice > 0).ToList().MapToList<AfterSaleBonusDetailDto>();

        }

     

        /// <summary>
        /// 校验提交售后提成申请是否符合条件
        /// </summary>
        /// <param name="addOrUpdateSaleBonusApplicationInfo"></param>
        /// <returns></returns>
        private async Task CheckAfterSaleBonusApplication(AddOrUpdateAfterSaleBonusApplicationInfo addOrUpdateSaleBonusApplicationInfo)
        {
            if (!addOrUpdateSaleBonusApplicationInfo.saleBonusHeaderDto.DocEntry.HasValue)
            {
                throw new NotImplementedException("申请提成销售订单号不可为空");
            }
            var docEntry = addOrUpdateSaleBonusApplicationInfo.saleBonusHeaderDto.DocEntry.Value;
            var ordrInfos = await (from a in UnitWork.Find<ORDR>(null)
                                   join b in UnitWork.Find<OrdrStat>(null) on a.DocEntry.ToString() equals b.Code
                                   where a.DocEntry == docEntry && a.DocStatus == "C" && a.CANCELED == "N"
                                   select new { a.DocEntry, a.DocTotal, b.U_BillStatus, b.U_ReceivePayStatus }).FirstOrDefaultAsync();
            var adjustInfo = await UnitWork.Find<FinanceBonusFee>(null).Where(x => x.OrderNo.Value == docEntry).ToListAsync();

            if (ordrInfos == null)
            {
                throw new NotImplementedException("未查询到相应有效销售订单");
            }
            if (ordrInfos.U_BillStatus != (int)OpenAuth.Repository.Domain.Settlement.BillStatus.Bill)
            {
                throw new NotImplementedException("该订单未开完发票，无法申请提成");
            }
            if (ordrInfos.U_ReceivePayStatus != 1)
            {
                throw new NotImplementedException("该订单未收完款，无法申请提成");
            }
            if (addOrUpdateSaleBonusApplicationInfo.saleBonusHeaderDto.ReceiptAmount + adjustInfo.Sum(x => x.FeeAmount)
                != addOrUpdateSaleBonusApplicationInfo.saleBonusHeaderDto.DeliveryAmount)
            {
                throw new NotImplementedException("该订单帐不平（调整金额与收款金额之和不等于发货金额。）");
            }
            if (!await IsRightTime())
            {
                throw new NotImplementedException("当前时间点不允许提交售后提成");
            }
        }

        /// <summary>
        /// 是否在限制时间内
        /// </summary>
        /// <returns></returns>
        private async Task<bool> IsRightTime()
        {
            var bonusTimeConfig = await UnitWork.Find<Category>(x => x.TypeId == FinanceConsts.SaleBonusApplyLimit)
                .ToDictionaryAsync(x => x.DtCode, x => x.DtValue);

            var nowTime = DateTime.Now;
            int currentQuarter = (nowTime.Month - 1) / 3 + 1;

            string configKey = $"LimitTime-Q{currentQuarter}";

            if (bonusTimeConfig.TryGetValue(configKey, out string limitTime))
            {
                string currentDateStr = nowTime.ToString("MM-dd");
                return currentDateStr.CompareTo(limitTime) <= 0;
            }

            return true;
        }

        /// <summary>
        /// 
        /// </summary>
        /// <param name="addOrUpdateSaleBonusApplicationInfo"></param>
        /// <returns></returns>
        public async Task<Infrastructure.Response> AddOrUpdateAfterSaleBonusApplication(AddOrUpdateAfterSaleBonusApplicationInfo addOrUpdateSaleBonusApplicationInfo)
        {
            if (!addOrUpdateSaleBonusApplicationInfo.IsDraft)
                await CheckAfterSaleBonusApplication(addOrUpdateSaleBonusApplicationInfo);
            var docEntry = addOrUpdateSaleBonusApplicationInfo.saleBonusHeaderDto.DocEntry;

            var bonusApplicationExist = await UnitWork.IsExistAsync<FinanceBonusApplication>(x => x.OrderNo == docEntry);
            if (!bonusApplicationExist)
            {
                await AddAfterSaleBonusApplication(addOrUpdateSaleBonusApplicationInfo);
            }
            else
            {
                await UpdateAfterSaleBonusApplication(addOrUpdateSaleBonusApplicationInfo);
            }
            return new Infrastructure.Response();
        }
        /// <summary>
        /// 新增售后提成申请
        /// </summary>
        /// <param name="addOrUpdateSaleBonusApplicationInfo"></param>
        /// <returns></returns>
        private async Task AddAfterSaleBonusApplication(AddOrUpdateAfterSaleBonusApplicationInfo addOrUpdateSaleBonusApplicationInfo)
        {
            var waitAddApplicationInfo = ConvertToSaleBonusApplicationDbDto(addOrUpdateSaleBonusApplicationInfo);
            User user = _auth.GetCurrentUser().User;
            var userDepartment = await (from a in UnitWork.Find<Relevance>(r => r.Key == Define.USERORG)
                                        join c in UnitWork.Find<Repository.Domain.Org>(null) on a.SecondId equals c.Id
                                        join d in UnitWork.Find<User>(null) on a.FirstId equals d.Id
                                        where user.Id == a.FirstId
                                        select new { OrgName = c.Name, a.FirstId, UserName = d.Name }).FirstOrDefaultAsync();
            using (var transaction = await UnitWork.GetDbContext<FinanceBonusApplication>().Database.BeginTransactionAsync())
            {


                //不是草稿，需要创建流程
                if (!addOrUpdateSaleBonusApplicationInfo.IsDraft)
                {
                    //创建售后提成流程
                    var mf = await UnitWork.Find<FlowScheme>(a => a.SchemeName == SchemeName).FirstOrDefaultAsync();
                    var afir = new AddFlowInstanceReq();
                    afir.SchemeId = mf.Id;
                    afir.FrmType = 2;
                    afir.Code = DatetimeUtil.ToUnixTimestampByMilliseconds(DateTime.Now).ToString();
                    afir.CustomName = $"售后提成" + DateTime.Now;
                    afir.FrmData = "";
                    afir.CreateUserId = user.Id;
                    afir.CreateUserName = user.Name;
                    var Flowinstance = await _flowInstanceApp.CreateInstanceAndGetModelAsync(afir);
                    waitAddApplicationInfo.FlowInstanceId = Flowinstance.Id;
                    waitAddApplicationInfo.GlobalApprovalId = _globalIdentityApp.GetNumId(GlobalOrderType.售后提成, EnumUtility.GetDescription(GlobalOrderType.售后提成));
                }

                await UnitWork.AddAsync<FinanceBonusApplication, int>(waitAddApplicationInfo);
                await UnitWork.SaveAsync();

                if (!addOrUpdateSaleBonusApplicationInfo.IsDraft)
                {
                    //售后提成操作记录
                    FinanceSaleBonusOperationHis operationHis = new FinanceSaleBonusOperationHis
                    {
                        Action = "提交",
                        CreateTime = DateTime.Now,
                        CreateUserId = user.Id,
                        SaleBonusId = waitAddApplicationInfo.Id,
                        Remark = "售后提成申请",
                        ApprovalResult = "同意",
                        IntervalTime = 0,
                        CreateUser = userDepartment?.OrgName + "-" + userDepartment?.UserName,
                    };
                    await UnitWork.AddAsync<FinanceSaleBonusOperationHis, int>(operationHis);
                    await UnitWork.SaveAsync();
                    await UpdateBonusInfoToOrdrStat(new List<(int, int)> { (waitAddApplicationInfo.OrderNo.Value, 1) });
                }


                await transaction.CommitAsync();
            }
        }

        /// <summary>
        /// 页面详情转换成实体
        /// </summary>
        /// <param name="addOrUpdateSaleBonusApplicationInfo"></param>
        /// <returns></returns>
        private FinanceBonusApplication ConvertToSaleBonusApplicationDbDto(AddOrUpdateAfterSaleBonusApplicationInfo addOrUpdateSaleBonusApplicationInfo)
        {
            var user = _auth.GetCurrentUser().User;
            var headerInfo = addOrUpdateSaleBonusApplicationInfo.saleBonusHeaderDto;
            var detailInfos = addOrUpdateSaleBonusApplicationInfo.saleBonusItemDetailDtos;
            //主表
            FinanceBonusApplication bonusApplication = new FinanceBonusApplication
            {
                OrderNo = headerInfo.DocEntry,
                BonusType = Repository.Domain.Settlement.BonusType.售后提成,
                Indicator = headerInfo.Indicator,
                CardCode = headerInfo.CardCode,
                CardName = headerInfo.CardName,
                BonusTotalAmount = detailInfos.Sum(x => x.BonusAmount),
                BonusStatus = addOrUpdateSaleBonusApplicationInfo.IsDraft ? BonusApplicationStatus.草稿 : BonusApplicationStatus.审批中,
                ApplyUserId = user.Id,
                CreateTime = DateTime.Now,
                UpdateTime = DateTime.Now,
                DeliveryTotalAmount = headerInfo.DeliveryAmount ?? 0,
                TransportAmount = 0,
                DeductTotalAmount = 0,
                AdjustTotalAmount = headerInfo.AdjustAmount ?? 0,
                ReceiptTotalAmount = headerInfo.ReceiptAmount ?? 0,
                OrderAmount = headerInfo.OrderAmount ?? 0,
                SubOrderNo = headerInfo.SubOrderNo,
                AfterTaxAccessoryTotalAmount = 0,
                AfterSaleTotalAmount = detailInfos.Sum(x => x.AfterSalesSetTotal),
            };
            //明细
            List<FinanceSaleBonusDetails> financeSaleBonusDetails = new List<FinanceSaleBonusDetails>();

            detailInfos.ForEach(detailInfo =>
            {
                //明细扣减
                List<FinanceSaleBonusDeductDetail> financeSaleBonusDeductDetails = new List<FinanceSaleBonusDeductDetail>();
                var financeSaleBonusDetail = new FinanceSaleBonusDetails
                {
                    Id = detailInfo.Id,
                    FinanceBonusId = bonusApplication.Id,
                    SaleItemCode = detailInfo.SaleItemCode,
                    ItemDescribtion = detailInfo.ItemDescribtion,
                    ItemCount = detailInfo.ItemCount,
                    UnitPrice = detailInfo.UnitPrice,
                    TotalAmount = detailInfo.LineTotal,
                    StockPrice = detailInfo.ItemCost,
                    AfterSalePrice = detailInfo.AfterSalesSetPrc,
                    PrcUnitPrice = detailInfo.PurUnitPrice,
                    ShippingCost = 0,
                    BonusPercent = 0,
                    OtherFees =  0,
                    ExchangeRateDiffAmount = detailInfo.ExchangeRateDiffAmount ?? 0,
                    DiscountAmount =  0,
                    TotalDeductAmount = 0,
                    BonusAmount = detailInfo.BonusAmount ,
                    AfterTaxAccessoryAmount =  0,
                    
                };
                financeSaleBonusDetails.Add(financeSaleBonusDetail);
                detailInfo.DeductInfos.ForEach(deductInfo =>
                {
                    financeSaleBonusDeductDetails.Add(new FinanceSaleBonusDeductDetail
                    {
                        Id = deductInfo.Id ?? 0,
                        SaleBonusDetailId = financeSaleBonusDetail.Id,
                        SaleBonusApplicationId = bonusApplication.Id,
                        DeductionCollectionId = deductInfo.DeductionId,
                        DeductAmount = deductInfo.DeductAmount,
                        DeductionName = deductInfo.DeductionName,
                    });
                });
                financeSaleBonusDetail.financeSaleBonusDeductDetails = financeSaleBonusDeductDetails;
            });
            bonusApplication.financeSaleBonusDetails = financeSaleBonusDetails;
            return bonusApplication;
        }
        /// <summary>
        /// 更新售后提成申请
        /// </summary>
        /// <param name="addOrUpdateSaleBonusApplicationInfo"></param>
        /// <returns></returns>
        /// <exception cref="NotImplementedException"></exception>
        private async Task UpdateAfterSaleBonusApplication(AddOrUpdateAfterSaleBonusApplicationInfo addOrUpdateSaleBonusApplicationInfo)
        {
            var user = _auth.GetCurrentUser().User;
            var docEntry = addOrUpdateSaleBonusApplicationInfo.saleBonusHeaderDto.DocEntry;
            var bonusApplicationInfo = await UnitWork.Find<FinanceBonusApplication>(null)
                                        .Where(x => x.OrderNo == docEntry && x.BonusType == Repository.Domain.Settlement.BonusType.售后提成)
                                        .FirstOrDefaultAsync();
            var applicationDetails = await UnitWork.Find<FinanceSaleBonusDetails>(null).Where(x => x.FinanceBonusId == bonusApplicationInfo.Id).ToListAsync();
            var applicationDetailIds = applicationDetails.Select(x => x.Id).ToList();
            var applicationDetailDeducts = await UnitWork.Find<FinanceSaleBonusDeductDetail>(null).Where(x => applicationDetailIds.Contains(x.SaleBonusDetailId.Value)).ToListAsync();
            if (bonusApplicationInfo == null)
            {
                throw new NotImplementedException($"需更新的订单({docEntry})售后提成不存在");
            }
            if (bonusApplicationInfo.BonusStatus != BonusApplicationStatus.草稿 && bonusApplicationInfo.BonusStatus != BonusApplicationStatus.已驳回)
            {
                throw new NotImplementedException($"售后提成({docEntry})状态不为草稿或驳回，不允许更改");
            }
            var waitUpdateApplicationInfo = ConvertToSaleBonusApplicationDbDto(addOrUpdateSaleBonusApplicationInfo);
            var userDepartment = await (from a in UnitWork.Find<Relevance>(r => r.Key == Define.USERORG)
                                        join c in UnitWork.Find<Repository.Domain.Org>(null) on a.SecondId equals c.Id
                                        join d in UnitWork.Find<User>(null) on a.FirstId equals d.Id
                                        where user.Id == a.FirstId
                                        select new { OrgName = c.Name, a.FirstId, UserName = d.Name }).FirstOrDefaultAsync();
            waitUpdateApplicationInfo.Id = bonusApplicationInfo.Id;
            waitUpdateApplicationInfo.FlowInstanceId = bonusApplicationInfo.FlowInstanceId;
            waitUpdateApplicationInfo.BonusStatus = bonusApplicationInfo.BonusStatus;
            waitUpdateApplicationInfo.CreateTime = bonusApplicationInfo.CreateTime;
            bonusApplicationInfo.UpdateTime = DateTime.Now;
            using (var transaction = await UnitWork.GetDbContext<FinanceBonusApplication>().Database.BeginTransactionAsync())
            {


                //不是草稿,且未创建过流程，需要创建流程
                if (!addOrUpdateSaleBonusApplicationInfo.IsDraft && string.IsNullOrEmpty(waitUpdateApplicationInfo.FlowInstanceId))
                {
                    //创建报销流程
                    var mf = await UnitWork.Find<FlowScheme>(a => a.SchemeName == SchemeName).FirstOrDefaultAsync();
                    var afir = new AddFlowInstanceReq();
                    afir.SchemeId = mf.Id;
                    afir.FrmType = 2;
                    afir.Code = DatetimeUtil.ToUnixTimestampByMilliseconds(DateTime.Now).ToString();
                    afir.CustomName = $"售后提成" + DateTime.Now;
                    afir.FrmData = "";
                    afir.CreateUserId = user.Id;
                    afir.CreateUserName = user.Name;
                    var Flowinstance = await _flowInstanceApp.CreateInstanceAndGetModelAsync(afir);
                    waitUpdateApplicationInfo.FlowInstanceId = Flowinstance.Id;
                    waitUpdateApplicationInfo.BonusStatus = BonusApplicationStatus.审批中;

                    //售后提成操作记录
                    FinanceSaleBonusOperationHis operationHis = new FinanceSaleBonusOperationHis
                    {
                        Action = "提交",
                        CreateTime = DateTime.Now,
                        CreateUserId = user.Id,
                        SaleBonusId = waitUpdateApplicationInfo.Id,
                        Remark = "售后提成申请",
                        ApprovalResult = "同意",
                        IntervalTime = 0,
                        CreateUser = userDepartment?.OrgName + "-" + userDepartment?.UserName,
                    };
                    waitUpdateApplicationInfo.BonusStatus = BonusApplicationStatus.审批中;
                    await UnitWork.AddAsync<FinanceSaleBonusOperationHis, int>(operationHis);
                    await UnitWork.SaveAsync();
                    await UpdateBonusInfoToOrdrStat(new List<(int, int)> { (bonusApplicationInfo.OrderNo.Value, 1) });
                }
                if (!addOrUpdateSaleBonusApplicationInfo.IsDraft && !waitUpdateApplicationInfo.GlobalApprovalId.HasValue)
                {
                    waitUpdateApplicationInfo.GlobalApprovalId = _globalIdentityApp.GetNumId(GlobalOrderType.售后提成, EnumUtility.GetDescription(GlobalOrderType.售后提成));
                }

                await UnitWork.BatchDeleteAsync(applicationDetailDeducts.ToArray());
                await UnitWork.BatchDeleteAsync(applicationDetails.ToArray());
                await UnitWork.SaveAsync();

                var waitAddItemDetails = waitUpdateApplicationInfo.financeSaleBonusDetails;
                waitAddItemDetails.ForEach(x => x.FinanceBonusId = waitUpdateApplicationInfo.Id);
                await UnitWork.BatchAddAsync<FinanceSaleBonusDetails, int>(waitAddItemDetails.ToArray());
                await UnitWork.SaveAsync();

                if (waitUpdateApplicationInfo.BonusStatus == BonusApplicationStatus.已驳回 && !addOrUpdateSaleBonusApplicationInfo.IsDraft && !string.IsNullOrEmpty(waitUpdateApplicationInfo.FlowInstanceId))
                {
                    // 重新发起流程
                    var verificationReq = new VerificationReq()
                    {
                        FlowInstanceId = waitUpdateApplicationInfo.FlowInstanceId,
                        VerificationFinally = VerificationFinallyType.agree,
                        VerificationOpinion = "售后提成重新发起申请",
                        Operator = user,
                    };
                    await _flowInstanceApp.Verification(verificationReq);
                    waitUpdateApplicationInfo.BonusStatus = BonusApplicationStatus.审批中;

                    //售后提成操作记录
                    FinanceSaleBonusOperationHis operationHis = new FinanceSaleBonusOperationHis
                    {
                        Action = "提交",
                        CreateTime = DateTime.Now,
                        CreateUserId = user.Id,
                        SaleBonusId = waitUpdateApplicationInfo.Id,
                        Remark = "售后提成申请",
                        ApprovalResult = "同意",
                        IntervalTime = 0,
                        CreateUser = userDepartment?.OrgName + "-" + userDepartment?.UserName,
                    };
                    await UnitWork.AddAsync<FinanceSaleBonusOperationHis, int>(operationHis);
                    await UnitWork.SaveAsync();
                }
                await UnitWork.UpdateAsync(waitUpdateApplicationInfo);
                await UnitWork.SaveAsync();
                await transaction.CommitAsync();
            }

        }
    }
}
