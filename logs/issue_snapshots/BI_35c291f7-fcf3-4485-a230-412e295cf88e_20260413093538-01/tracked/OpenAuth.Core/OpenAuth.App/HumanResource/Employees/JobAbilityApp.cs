using Infrastructure;
using Infrastructure.Cache;
using Microsoft.EntityFrameworkCore;
using Npoi.Mapper;
using NStandard;
using OpenAuth.App.CommonHelp;
using OpenAuth.App.Dto;
using OpenAuth.App.Employees.Dtos;
using OpenAuth.App.Employees.Dtos.JobAbilityDto;
using OpenAuth.App.Employees.Enum;
using OpenAuth.App.Finance.Interfaces;
using OpenAuth.App.Finance.Request;
using OpenAuth.App.HumanResource.Employees.Dtos;
using OpenAuth.App.Interface;
using OpenAuth.App.MD.Helper;
using OpenAuth.App.Response;
using OpenAuth.Repository;
using OpenAuth.Repository.Domain;
using OpenAuth.Repository.Domain.Engineers;
using OpenAuth.Repository.Domain.MD.ObjectLabel;
using OpenAuth.Repository.Domain.MD.Train;
using OpenAuth.Repository.Domain.Technicians;
using OpenAuth.Repository.Interface;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading.Tasks;
using TencentCloud.Cwp.V20180228.Models;

namespace OpenAuth.App.Employees
{
    /// <summary>
    /// 岗位技能配置
    /// </summary>
    public class JobAbilityApp : OnlyUnitWorkBaeApp, IJobAbilityApp
    {
        private readonly IEmployeeApp _employeeApp;
        private readonly IAppUserMapApp _appUserMapApp;
        private readonly CacheContextBase _cache;
        private readonly IFinanceSummaryApp _financeSummaryApp;
        private readonly IFinanceDataApp _financeDataApp;
        /// <summary>
        /// 构造器
        /// </summary>
        /// <param name="unitWork"></param>
        /// <param name="auth"></param>
        /// <param name="employeeApp"></param>
        /// <param name="appUserMapApp"></param>
        /// <param name="cache"></param>
        /// <param name="financeSummaryApp"></param>
        /// <param name="financeDataApp"></param>
        public JobAbilityApp(
            IUnitWork unitWork,
            IAuth auth,
            IEmployeeApp employeeApp,
            IAppUserMapApp appUserMapApp,
            CacheContextBase cache,
            IFinanceSummaryApp financeSummaryApp,
            IFinanceDataApp financeDataApp) : base(unitWork, auth)
        {
            _employeeApp = employeeApp;
            _appUserMapApp = appUserMapApp;
            _cache = cache;
            _financeSummaryApp = financeSummaryApp;
            _financeDataApp = financeDataApp;
        }

        #region 根据岗位获取技能代码

        /// <summary>
        /// 根据岗位获取技能代码
        /// </summary>
        /// <returns></returns>
        /// <exception cref="CommonException"></exception>
        public async Task<TableData> GetAbilitySkill(JobPosition jobAbilityType)
        {
            var config = await UnitWork.Find<JobAbilityConfig>(x => x.JobPosition == (int)jobAbilityType)
                .Where(x => x.Status == (int)AbilityStatus.启用)
                .OrderBy(x => x.SortNum)
                .Select(x => new
                {
                    x.AbilityCode,
                    x.AbilityType,
                    x.AbilityRemark
                }).ToListAsync();
            return new TableData { Data = config };
        }


        /// <summary>
        /// 根据岗位和用户ID获取技能信息（未定级技能等级置为0）
        /// </summary>
        public async Task<List<UserAbilitySkillDto>> GetUserAbilitySkills(JobPosition jobPosition, string userId)
        {
            // 获取岗位配置
            var configQuery = UnitWork.Find<JobAbilityConfig>(x =>
                x.JobPosition == (int)jobPosition &&
                x.Status == (int)AbilityStatus.启用)
                .OrderBy(x => x.SortNum);
            List<AbilityQueryResult> result;
            List<AbilityQueryResult> result2;
            // 根据岗位类型选择技能表
            if (jobPosition == JobPosition.Technician)
            {
                // 技术员岗位 - 使用 TechnicianSkillAbility 表
                var techQuery = from config in configQuery
                                join skill in UnitWork.Find<TechnicianSkillAbility>(x => x.UserId == userId)
                                    on config.Id equals skill.JobAbilityId into skillGroup
                                from userSkill in skillGroup.DefaultIfEmpty()
                                select new AbilityQueryResult
                                {
                                    Id = config.Id,
                                    AbilityCode = config.AbilityCode,
                                    AbilityRemark = config.AbilityRemark,
                                    AbilityType = config.AbilityType,
                                    AbilityValue = userSkill != null ? userSkill.JobAbilityGrade : null
                                };

                result = await techQuery.Where(x => x.AbilityValue != null).ToListAsync();
                result2 = await techQuery.ToListAsync();
                if(result.Count == 0)
                {
                    result.Add(result2.FirstOrDefault(x=>x.AbilityCode != "U"));
                    result.Add(result2.FirstOrDefault(x=>x.AbilityCode == "U"));
                }
            }
            else if (jobPosition == JobPosition.Salesman)
            {
                // 销售员岗位 - 使用 SalesmanSkillAbility 表
                var salesQuery = from config in configQuery
                                 join skill in UnitWork.Find<SalesmanSkillAbility>(x => x.UserId == userId)
                                     on config.Id equals skill.JobAbilityId into skillGroup
                                 from userSkill in skillGroup.DefaultIfEmpty()
                                 select new AbilityQueryResult
                                 {
                                     Id = config.Id,
                                     AbilityCode = config.AbilityCode,
                                     AbilityRemark = config.AbilityRemark,
                                     AbilityType = config.AbilityType,
                                     AbilityValue = userSkill != null ? userSkill.JobAbilityGrade : null
                                 };

                result = await salesQuery.ToListAsync();
                result2 = result;
            }
            else if (jobPosition == JobPosition.RDEngineer)
            {
                // 研发工程师岗位 - 使用 RDEngineerSkillAbility 表
                var rdQuery = from config in configQuery
                              join skill in UnitWork.Find<RDEngineerSkillAbility>(x => x.UserId == userId)
                                  on config.Id equals skill.JobAbilityId into skillGroup
                              from userSkill in skillGroup.DefaultIfEmpty()
                              select new AbilityQueryResult
                              {
                                  Id = config.Id,
                                  AbilityCode = config.AbilityCode,
                                  AbilityRemark = config.AbilityRemark,
                                  AbilityType = config.AbilityType,
                                  AbilityValue = userSkill != null ? userSkill.JobAbilityGrade : null
                              };

                result = await rdQuery.ToListAsync();
                result2 = result;
            }
            else
            {
                result = new List<AbilityQueryResult>();
                result2 = result;
            }
       
            var configIds= result2.Select(x => x.Id).ToList();
            var grades = await UnitWork.Find<JobAbilityGradeConfig>(x => configIds.Contains(x.JobAblilityConfigId)).ToListAsync();

             // 按技能配置ID分组等级
            var gradeGroups = grades
                .GroupBy(g => g.JobAblilityConfigId)
                .ToDictionary(g => g.Key, g => g.ToList());
    
            // 构建最终响应数据结构
            var responseData = result.Select(item => new UserAbilitySkillDto
            {
                AbilityCode = item.AbilityCode,
                AbilityRemark = item.AbilityRemark,
                AbilityValue = item.AbilityValue ?? gradeGroups[item.Id]
                    .OrderBy(g => g.SortNum).FirstOrDefault()?.JobAbilityGradeCode?? string.Empty,
                AbilityType = item.AbilityType,
                AbilityGrades = gradeGroups.ContainsKey(item.Id)
                ? gradeGroups[item.Id]
                    .OrderBy(g => g.SortNum)
                    .Select(g => new AbilityGradeDto
                    {
                        Code = g.JobAbilityGradeCode,
                        SortNum = g.SortNum
                    })
                    .ToList()
                : new List<AbilityGradeDto>() // 空列表
            }).ToList();
            if(jobPosition == JobPosition.Technician)
            {
                responseData.Where(x => x.AbilityCode != "U").ForEach(x => x.SwitchUserAbilitySkillOption = result2
                .Where(x=>x.AbilityCode != "U")
                .Select(item => new UserAbilitySkillDto
                {
                    AbilityCode = item.AbilityCode,
                    AbilityRemark = item.AbilityRemark,
                    AbilityValue = item.AbilityValue ?? (gradeGroups.ContainsKey(item.Id) ? gradeGroups[item.Id]
                    .OrderBy(g => g.SortNum).FirstOrDefault()?.JobAbilityGradeCode ?? string.Empty :string.Empty),
                    AbilityType = item.AbilityType,
                    AbilityGrades = gradeGroups.ContainsKey(item.Id)
                ? gradeGroups[item.Id]
                    .OrderBy(g => g.SortNum)
                    .Select(g => new AbilityGradeDto
                    {
                        Code = g.JobAbilityGradeCode,
                        SortNum = g.SortNum
                    })
                    .ToList()
                : new List<AbilityGradeDto>() // 空列表
                }).ToList());
            }
            
            return responseData;
        }
        #endregion

        #region 获取技能详情
        /// <summary>
        /// 根据主键获取对应技能等级集合
        /// </summary>
        /// <returns></returns>
        /// <exception cref="CommonException"></exception>
        public async Task<List<AbilityCodeListDto>> GetAbilityByKeyId(GetAbilityByKeyIdReq req)
        {
            var result = new List<AbilityCodeListDto>();
            switch (req.JobAbilityType)
            {
                case JobPosition.Technician:
                    result = await GetTechAbilityByKeyId(req.KeyId);
                    break;
                case JobPosition.Salesman: break;
                case JobPosition.RDEngineer:
                    result = await GetRDEngineerAbilityByKeyId(req.KeyId);
                    break;
                default: break;
            }
            return result;
        }

        /// <summary>
        /// 获取技术员技能等级详情
        /// </summary>
        /// <param name="keyId"></param>
        /// <returns></returns>
        private async Task<List<AbilityCodeListDto>> GetTechAbilityByKeyId(string keyId)
        {
            var query = await (from jobAbility in UnitWork.Find<JobAbilityConfig>(x => x.JobPosition == (int)JobPosition.Technician)
                        .Where(x => x.Status == (int)AbilityStatus.启用)
                               join techSkill in UnitWork.Find<TechnicianSkillAbility>(x => x.UserId == keyId)
                               on jobAbility.Id equals techSkill.JobAbilityId into techSkills
                               from techSkill in techSkills.DefaultIfEmpty()
                               orderby jobAbility.SortNum
                               select new AbilityCodeListDto
                               {
                                   AbilityCode = jobAbility.AbilityCode,
                                   AbilityType = (AbilityType)jobAbility.AbilityType,
                                   AbilityValue = techSkill != null ? techSkill.JobAbilityGrade : "0"
                               }).ToListAsync();
            if (query.All(x => x.AbilityValue == "0"))
            {
                query.RemoveAll(x => x.AbilityCode == "B" || x.AbilityCode == "C");
            }
            else
            {
                query = query.Where(x => x.AbilityValue != "0" || x.AbilityCode == "U").ToList();
            }
            return query;
        }

        /// <summary>
        /// 获取研发工程师技能等级详情
        /// </summary>
        /// <param name="keyId"></param>
        /// <returns></returns>
        private async Task<List<AbilityCodeListDto>> GetRDEngineerAbilityByKeyId(string keyId)
        {
            var query = await (from jobAbility in UnitWork.Find<JobAbilityConfig>(x => x.JobPosition == (int)JobPosition.RDEngineer)
                        .Where(x => x.Status == (int)AbilityStatus.启用)
                               join rdSkill in UnitWork.Find<RDEngineerSkillAbility>(x => x.UserId == keyId)
                               on jobAbility.Id equals rdSkill.JobAbilityId into rdSkills
                               from rdSkill in rdSkills.DefaultIfEmpty()
                               orderby jobAbility.SortNum
                               select new AbilityCodeListDto
                               {
                                   AbilityCode = jobAbility.AbilityCode,
                                   AbilityType = (AbilityType)jobAbility.AbilityType,
                                   AbilityValue = rdSkill != null ? rdSkill.JobAbilityGrade : "0"
                               }).ToListAsync();
            return query;
        }

        
        #endregion

        #region 新增、更新技能
        /// <summary>
        /// 晋升用户技能等级
        /// </summary>
        /// <param name="jobPosition">岗位</param>
        /// <param name="jobAbilityId"></param>
        /// <param name="jobAbilityGrade"></param>
        /// <param name="userId">待更新技能用户ID</param>
        /// <returns></returns>
        /// <exception cref="CommonException"></exception>
        public async Task UpgradeUserAbility(JobPosition jobPosition, string jobAbilityId, string jobAbilityGrade, string userId)
        {
            var loginContext = _auth.GetCurrentUser() ?? throw new CommonException(Define.LoginHasExpired, Define.INVALID_TOKEN);
            switch (jobPosition)
            {
                case JobPosition.Technician:
                    var techAbility = await UnitWork.Find<TechnicianSkillAbility>(o => o.UserId == userId && o.JobAbilityId == jobAbilityId).FirstOrDefaultAsync();
                    if (techAbility == null)
                    {
                        //等于null说明没有技能等级，初次获得岗位技能
                        var newAbility = new TechnicianSkillAbility()
                        {
                            UserId = userId,
                            JobAbilityId = jobAbilityId,
                            JobAbilityGrade = jobAbilityGrade,
                            CreateTime = DateTime.Now,
                            CreateUserId = loginContext.User.Id,
                            CreateName = loginContext.User.Name,
                            UpdateTime = DateTime.Now,
                            UpdateId = loginContext.User.Id,
                            UpdateName = loginContext.User.Name
                        };
                        await UnitWork.AddAsync(newAbility);
                        await UnitWork.SaveAsync();
                    }
                    else
                    {
                        //已经获得岗位技能，现在晋升
                        techAbility.JobAbilityGrade = jobAbilityGrade;
                        techAbility.UpdateTime = DateTime.Now;
                        techAbility.UpdateId = loginContext.User.Id;
                        techAbility.UpdateName = loginContext.User.Name;
                        await UnitWork.UpdateAsync(techAbility);
                        await UnitWork.SaveAsync();
                    }
                    break;
                case JobPosition.Salesman:
                    var saleAbility = await UnitWork.Find<SalesmanSkillAbility>(o => o.UserId == userId && o.JobAbilityId == jobAbilityId).FirstOrDefaultAsync();
                    if (saleAbility == null)
                    {
                        //等于null说明没有技能等级，初次获得岗位技能
                        var newAbility = new SalesmanSkillAbility()
                        {
                            UserId = userId,
                            JobAbilityId = jobAbilityId,
                            JobAbilityGrade = jobAbilityGrade,
                            CreateTime = DateTime.Now,
                            CreateUserId = loginContext.User.Id,
                            CreateName = loginContext.User.Name,
                            UpdateTime = DateTime.Now,
                            UpdateId = loginContext.User.Id,
                            UpdateName = loginContext.User.Name
                        };
                        await UnitWork.AddAsync(newAbility);
                        await UnitWork.SaveAsync();
                    }
                    else
                    {
                        //已经获得岗位技能，现在晋升
                        saleAbility.JobAbilityGrade = jobAbilityGrade;
                        saleAbility.UpdateTime = DateTime.Now;
                        saleAbility.UpdateId = loginContext.User.Id;
                        saleAbility.UpdateName = loginContext.User.Name;
                        await UnitWork.UpdateAsync(saleAbility);
                        await UnitWork.SaveAsync();
                    }
                    break;
                case JobPosition.RDEngineer:
                    var rdAbility = await UnitWork.Find<RDEngineerSkillAbility>(o => o.UserId == userId && o.JobAbilityId == jobAbilityId).FirstOrDefaultAsync();
                    if (rdAbility == null)
                    {
                        //等于null说明没有技能等级，初次获得岗位技能
                        var newAbility = new RDEngineerSkillAbility()
                        {
                            UserId = userId,
                            JobAbilityId = jobAbilityId,
                            JobAbilityGrade = jobAbilityGrade,
                            CreateTime = DateTime.Now,
                            CreateUserId = loginContext.User.Id,
                            CreateName = loginContext.User.Name,
                            UpdateTime = DateTime.Now,
                            UpdateId = loginContext.User.Id,
                            UpdateName = loginContext.User.Name
                        };
                        await UnitWork.AddAsync(newAbility);
                        await UnitWork.SaveAsync();
                    }
                    else
                    {
                        //已经获得岗位技能，现在晋升
                        rdAbility.JobAbilityGrade = jobAbilityGrade;
                        rdAbility.UpdateTime = DateTime.Now;
                        rdAbility.UpdateId = loginContext.User.Id;
                        rdAbility.UpdateName = loginContext.User.Name;
                        await UnitWork.UpdateAsync(rdAbility);
                        await UnitWork.SaveAsync();
                    }
                    break;
                default:
                    break;
            }
        }


        /// <summary>
        /// 新增更新技能
        /// </summary>
        /// <param name="req"></param>
        /// <returns></returns>
        public async Task AddOrUpdateAbility(AddOrUpdateAbilityReq req)
        {
            if (req.AbilityCodeListDtos == null || req.AbilityCodeListDtos.Count == 0)
                throw new CommonException($"请传入需要修改的技能等级", Define.Warning);
            await ModifyUserAbility(req);
            await SaveAbilityChangeHistory(req);
        }

        /// <summary>
        /// 修改用户技能等级
        /// </summary>
        /// <returns></returns>
        private async Task ModifyUserAbility(AddOrUpdateAbilityReq req)
        {
            var createUser = _auth.GetCurrentUser().User;
            #region 校验
            var user = await UnitWork.FindSingleAsync<User>(x => x.Id == req.KeyId && x.Status == 0);
            if (user == null)
                throw new CommonException($"技术员账号不存在，请检查", Define.Warning);
            var roles = await (from a in UnitWork.Find<Relevance>(r => r.Key == Define.USERROLE && r.FirstId == req.KeyId)
                               join b in UnitWork.Find<Role>(null)
                               on a.SecondId equals b.Id
                               select b.RoleKey
                               ).ToListAsync();
            if (req.JobAbilityType == JobPosition.Technician && !roles.Exists(x => x == RoleKeyConsts.After_Sale_Technician))
                throw new CommonException($"该用户未拥有售后技术员角色", Define.Warning);
            else if (req.JobAbilityType == JobPosition.Salesman && !roles.Exists(x => x == RoleKeyConsts.Sales_Man))
                throw new CommonException($"该用户未拥有销售员角色", Define.Warning);
            else if (req.JobAbilityType == JobPosition.RDEngineer && !roles.Exists(x => x == RoleKeyConsts.RD_Engineer))
                throw new CommonException($"该用户未拥有研发工程师角色", Define.Warning);

            var jobAbilityConfigs = await UnitWork.Find<JobAbilityConfig>(x => x.JobPosition == (int)req.JobAbilityType)
                .Where(x => x.Status == (int)AbilityStatus.启用)
                .OrderBy(x => x.SortNum)
                .ToListAsync();
            if (!req.AbilityCodeListDtos.All(x=> 
            jobAbilityConfigs.Exists(j=>j.AbilityCode== x.AbilityCode)))
                throw new CommonException($"技能类型不匹配，请检查", Define.Warning);
            #endregion

            switch (req.JobAbilityType)
            {
                case JobPosition.Technician:
                    var techSkill = await UnitWork.Find<TechnicianSkillAbility>(x => x.UserId == req.KeyId).ToListAsync();
                    techSkill = techSkill.Where(x => jobAbilityConfigs.Select(j => j.Id).Contains(x.JobAbilityId)).ToList();
                    if (!techSkill.Any())
                    {
                        var newTechSkills = req.AbilityCodeListDtos.Select(x => new TechnicianSkillAbility(createUser.Id, createUser.Name)
                        {
                            UserId = req.KeyId,
                            JobAbilityId = jobAbilityConfigs.Find(j => j.AbilityCode == x.AbilityCode).Id,
                            JobAbilityGrade = x.AbilityValue,
                        }).ToArray();
                        await UnitWork.BatchAddAsync(newTechSkills);
                        await UnitWork.SaveAsync();
                    }
                    else
                    {

                        var newTechSkills = req.AbilityCodeListDtos.Select(x => new TechnicianSkillAbility(createUser.Id, createUser.Name)
                        {
                            UserId = req.KeyId,
                            JobAbilityId = jobAbilityConfigs.Find(j => j.AbilityCode == x.AbilityCode).Id,
                            JobAbilityGrade = x.AbilityValue,
                        }).ToArray();
                        
                        await UnitWork.BatchDeleteAsync(techSkill.ToArray());
                        await UnitWork.BatchAddAsync(newTechSkills);
                        await UnitWork.SaveAsync();
                    }
                    break;
                case JobPosition.Salesman:
                    var salesmanSkill = await UnitWork.Find<SalesmanSkillAbility>(x => x.UserId == req.KeyId).ToListAsync();
                    salesmanSkill = salesmanSkill.Where(x => jobAbilityConfigs.Select(j => j.Id).Contains(x.JobAbilityId)).ToList();
                    if (!salesmanSkill.Any())
                    {
                        var addSkill = jobAbilityConfigs.Select(x => new SalesmanSkillAbility(createUser.Id, createUser.Name)
                        {
                            UserId = req.KeyId,
                            JobAbilityId = x.Id,
                            JobAbilityGrade = req.AbilityCodeListDtos.Find(a => a.AbilityCode == x.AbilityCode)?.AbilityValue ?? "0"
                        }).ToArray();
                        await UnitWork.BatchAddAsync(addSkill.ToArray());
                        await UnitWork.SaveAsync();
                    }
                    else
                    {
                        foreach (var item in salesmanSkill)
                        {
                            var jobAbility = jobAbilityConfigs.Find(x => x.Id == item.JobAbilityId);
                            var reqAbility = req.AbilityCodeListDtos.Find(a => a.AbilityCode == jobAbility.AbilityCode);
                            if(reqAbility == null)
                            {
                                continue;
                            }
                            item.JobAbilityGrade = reqAbility.AbilityValue;
                            item.UpdateTime = DateTime.Now;
                            item.UpdateId = createUser.Id;
                            item.UpdateName = createUser.Name;
                        }
                        await UnitWork.BatchUpdateAsync(salesmanSkill.ToArray());
                        await UnitWork.SaveAsync();
                    }
                    break;
                case JobPosition.RDEngineer:
                    var rdSkill = await UnitWork.Find<RDEngineerSkillAbility>(x => x.UserId == req.KeyId).ToListAsync();
                    rdSkill = rdSkill.Where(x => jobAbilityConfigs.Select(j => j.Id).Contains(x.JobAbilityId)).ToList();
                    if (!rdSkill.Any())
                    {
                        var newRdSkills = req.AbilityCodeListDtos.Select(x => new RDEngineerSkillAbility(createUser.Id, createUser.Name)
                        {
                            UserId = req.KeyId,
                            JobAbilityId = jobAbilityConfigs.Find(j => j.AbilityCode == x.AbilityCode).Id,
                            JobAbilityGrade = x.AbilityValue,
                        }).ToArray();
                        await UnitWork.BatchAddAsync(newRdSkills);
                        await UnitWork.SaveAsync();
                    }
                    else
                    {
                        var newRdSkills = req.AbilityCodeListDtos.Select(x => new RDEngineerSkillAbility(createUser.Id, createUser.Name)
                        {
                            UserId = req.KeyId,
                            JobAbilityId = jobAbilityConfigs.Find(j => j.AbilityCode == x.AbilityCode).Id,
                            JobAbilityGrade = x.AbilityValue,
                        }).ToArray();

                        await UnitWork.BatchDeleteAsync(rdSkill.ToArray());
                        await UnitWork.BatchAddAsync(newRdSkills);
                        await UnitWork.SaveAsync();
                    }
                    break;
                default:
                    break;
            }
        }

        /// <summary>
        /// 保存技能变更历史记录
        /// </summary>
        private async Task SaveAbilityChangeHistory(AddOrUpdateAbilityReq req)
        {
            var loginContext = _auth.GetCurrentUser();
            var jobAbilityConfigs = await UnitWork.Find<JobAbilityConfig>(x => x.JobPosition == (int)req.JobAbilityType)
                .Where(x => x.Status == (int)AbilityStatus.启用)
                .ToListAsync();

            var history = new JobAbilityChangeHistory
            {
                UserId = req.KeyId,
                JobPosition = (int)req.JobAbilityType,
                Reason = req.Reason,
                CreateTime = DateTime.Now,
                CreateId = loginContext.User.Id,
                CreateName = loginContext.User.Name
            };
            await UnitWork.AddAsync(history);

            var items = req.AbilityCodeListDtos.Select(x =>
            {
                var config = jobAbilityConfigs.Find(j => j.AbilityCode == x.AbilityCode);
                return new JobAbilityChangeHistoryItem
                {
                    ChangeHistoryId = history.Id,
                    AbilityCode = x.AbilityCode,
                    AbilityRemark = config?.AbilityRemark ?? x.AbilityCode,
                    AbilityValue = x.AbilityValue,
                    AbilityType = (int)x.AbilityType
                };
            }).ToArray();
            await UnitWork.BatchAddAsync(items);
            await UnitWork.SaveAsync();
        }

        /// <summary>
        /// 更新销售员业绩等级 - 弃用
        /// </summary>
        /// <returns></returns>
        public async Task UndateSalePerformance()
        {
            var salePerformanceConfig = await UnitWork.FindSingleAsync<JobAbilityConfig>(x => x.JobPosition == (int)JobPosition.Salesman && x.AbilityCode == "M") 
                ?? throw new InvalidOperationException("未找到销售员业绩等级配置");
            var salesmanSkills = await UnitWork.Find<SalesmanSkillAbility>(x => x.JobAbilityId == salePerformanceConfig.Id).ToListAsync();

            // 4.0UserId 到 slpCode 转换
            var userIds = salesmanSkills.Select(x => x.UserId).Distinct().ToArray();
            var nsapUserMap = await UnitWork.Find<NsapUserMap>(x => userIds.Contains(x.UserID)).ToListAsync();
            var nsapUserIds = nsapUserMap.Where(x => x.NsapUserId > 0).Select(x => (uint)x.NsapUserId.Value).ToList();
            var sboUsers = await UnitWork.Find<sbo_user>(x => x.sbo_id == Define.SBO_ID && nsapUserIds.Contains(x.user_id)).ToListAsync();

            var nowTime = DateTime.Now;
            var dict = await _financeSummaryApp.GetSalemanPerformances(nowTime);

            var updateSalesmanSkill = new List<SalesmanSkillAbility>();
            foreach (var item in salesmanSkills)
            {
                var user = nsapUserMap.FirstOrDefault(x => x.UserID == item.UserId);
                var sboUser = sboUsers.FirstOrDefault(x => x.user_id == (user?.NsapUserId ?? -1));
                var slpCode = sboUser?.sale_id ?? -1;
                if (slpCode <= 0)
                {
                    continue;
                }

                var totalAmount = dict.GetValueOrDefault(slpCode);
                var abilitySet = GetAbilitySet(totalAmount).ToString();
                if (item.JobAbilityGrade != abilitySet)
                {
                    item.JobAbilityGrade = abilitySet;
                    item.UpdateTime = nowTime;
                    item.UpdateId = Define.ADMINID;
                    item.UpdateName = Define.SUPERADMIN;
                    updateSalesmanSkill.Add(item);
                }
            }

            if (updateSalesmanSkill.Any())
            {
                await UnitWork.BatchUpdateAsync(updateSalesmanSkill.ToArray());
                await UnitWork.SaveAsync();
            }
        }
        /// <summary>
        /// 更新销售员业绩应收等级
        /// </summary>
        /// <returns></returns>
        public async Task UpdateSaleMrank()
        {
            var rRank = await UnitWork.Find<JobAbilityConfig>(x => x.AbilityCode == "M" && x.JobPosition == (int)JobPosition.Salesman).FirstOrDefaultAsync();
            if (rRank == null)
                return; //没有配置R级标准，不计算
            var now = DateTime.UtcNow;
            var oneYearAgo = DateTime.UtcNow.AddYears(-1);
            //一年以内的业绩
            var performanceInAYear = await _financeDataApp.GetSalePerformance(new GetSalePerformanceReq { BeginTime = oneYearAgo, EndTime = now });
            var userIds = performanceInAYear.Select(x => x.UserId).ToList();
            var salesManReceiptBalances = await _financeDataApp.GetSalesManReceiptBalance(userIds);
            var salesManRrankAbilits = await UnitWork.Find<SalesmanSkillAbility>(x => x.JobAbilityId == rRank.Id).ToListAsync();
            // 准备批量更新列表
            var toUpdate = new List<SalesmanSkillAbility>();
            var toAdd = new List<SalesmanSkillAbility>();
            foreach (var personPerformance in performanceInAYear)
            {
                //近一年的回款额
                var returnMoney = personPerformance.ReturnMoney;
                var receiptBalance = salesManReceiptBalances.FirstOrDefault(x => x.UserId == personPerformance.UserId)?.ReceiptBalance ?? 0M;
                var existSkill = salesManRrankAbilits.FirstOrDefault(x => x.UserId == personPerformance.UserId);
                // 处理零值情况
                if (returnMoney == 0 || receiptBalance == 0)
                {
                    if (existSkill != null)
                    {
                        existSkill.JobAbilityGrade = "1";
                        existSkill.UpdateTime = DateTime.UtcNow;
                        existSkill.UpdateId = Define.ADMINID;
                        existSkill.UpdateName = Define.SUPERADMIN;
                        toUpdate.Add(existSkill);
                    }
                    continue;
                }

                // 计算应收占比
                double ratio = (double)(receiptBalance / returnMoney);
                string grade;

                // 根据占比确定等级
                if (ratio > 0.6D)
                {
                    grade = "1"; // R-1
                }
                else if (ratio > 0.3D)
                {
                    grade = "2"; // R-2
                }
                else if (ratio > 0.15D)
                {
                    grade = "3"; // R-3
                }
                else
                {
                    grade = "4"; // R-4
                }

                // 更新或创建记录
                if (existSkill != null)
                {
                    existSkill.JobAbilityGrade = grade;
                    existSkill.UpdateName = Define.SUPERADMIN;
                    existSkill.UpdateId = Define.ADMINID;
                    existSkill.UpdateTime = DateTime.UtcNow;
                    toUpdate.Add(existSkill);
                }
                else
                {
                    toAdd.Add(new SalesmanSkillAbility
                    {
                        UserId = personPerformance.UserId,
                        JobAbilityId = rRank.Id,
                        JobAbilityGrade = grade,
                        CreateName = Define.SUPERADMIN,
                        CreateUserId = Define.ADMINID,
                        CreateTime = DateTime.UtcNow,
                        UpdateName = Define.SUPERADMIN,
                        UpdateId = Define.ADMINID,
                        UpdateTime = DateTime.UtcNow
                    });
                }
            }

            // 批量保存数据
            if (toUpdate.Any())
            {
                await UnitWork.BatchUpdateAsync(toUpdate.ToArray());
            }

            if (toAdd.Any())
            {
                await UnitWork.BatchAddAsync(toAdd.ToArray());
            }
            await UnitWork.SaveAsync();
        }
        /// <summary>
        /// 判断业务员进等级
        /// </summary>
        /// <param name="totalAmount"></param>
        /// <returns></returns>
        private static int GetAbilitySet(decimal totalAmount)
        {
            if (totalAmount <= 0)
            {
                return 0;
            }
            else if (totalAmount < 1200000)
            {
                return 1;
            }
            else if (totalAmount < 2400000)
            {
                return 2;
            }
            else if (totalAmount < 7200000)
            {
                return 3;
            }
            else
            {
                return 4;
            }
          
        }
        #endregion

        /// <summary>
        /// 获取用户技能等级
        /// </summary>
        /// <returns></returns>
        public async Task<Response<string>> GetUserAbility(int passPortId) 
        {
            //已开放技能等级的角色
            string[] openAbilityRole = new string[] { Define.After_Sale_Technician, Define.Sales_Man, Define.RD_Engineer };
            var roles = _cache.Get<List<RoleDto>>("Roles").Where(o => openAbilityRole.Contains(o.RoleKey));
            var userInfo = _appUserMapApp.GetUserByPassPortId(passPortId) ?? throw new CommonException("未查找到用户信息", 500);
            var userRoleIds = await UnitWork.Find<Relevance>(null)
                .Where(o => o.Key == Define.USERROLE)
                .Where(o => o.FirstId == userInfo.Id)
                .Select(o => o.SecondId).ToListAsync();
            try
            {
                //售后技术员
                if (userRoleIds.Exists(o => o == roles.FirstOrDefault(t => t.RoleKey == Define.After_Sale_Technician)?.Id))
                {
                    var userAbility = _employeeApp.GetUserAbilityGrade<TechnicianSkillAbility>(userInfo.Id);
                    if (!userAbility.Any())
                        throw new NotImplementedException("您的岗位暂未定义技能等级，请联系管理员吧！");
                    return new Response<string> { Result = AbilityHelper.CombinAbilityCodes(userAbility) };
                }
                //销售员
                else if (userRoleIds.Exists(o => o == roles.FirstOrDefault(t => t.RoleKey == Define.Sales_Man)?.Id))
                {
                    var userAbility = _employeeApp.GetUserAbilityGrade<SalesmanSkillAbility>(userInfo.Id);
                    if (!userAbility.Any())
                        throw new NotImplementedException("您的岗位暂未定义技能等级，请联系管理员吧！");
                    return new Response<string> { Result = AbilityHelper.CombinAbilityCodes(userAbility) };
                }
                //研发工程师
                else if (userRoleIds.Exists(o => o == roles.FirstOrDefault(t => t.RoleKey == Define.RD_Engineer)?.Id))
                {
                    var userAbility = _employeeApp.GetUserAbilityGrade<RDEngineerSkillAbility>(userInfo.Id);
                    if (!userAbility.Any())
                        throw new NotImplementedException("您的岗位暂未定义技能等级，请联系管理员吧！");
                    return new Response<string> { Result = AbilityHelper.CombinAbilityCodes(userAbility) };
                }
                else
                {
                    throw new NotImplementedException("您的岗位暂未定义技能等级，请联系管理员吧！");
                }
            }
            catch (Exception ex)
            {
                return new Response<string> { Code = 500, Message = ex.Message };
            }
        }

        /// <inheritdoc/>
        public async Task<Dictionary<string, string>> GetUserAbilityDict(List<string> userIds) 
        {
            var userAbilityDict = new Dictionary<string, string>();

			var jobAbilityConfigs = await UnitWork.Find<JobAbilityConfig>(null).Where(x => x.Status == (int)AbilityStatus.启用).ToListAsync();
			var technicians = await UnitWork.Find<TechnicianSkillAbility>(x => userIds.Contains(x.UserId)).ToListAsync();
			var salesmans = await UnitWork.Find<SalesmanSkillAbility>(x => userIds.Contains(x.UserId)).ToListAsync();
			var rdEngineers = await UnitWork.Find<RDEngineerSkillAbility>(x => userIds.Contains(x.UserId)).ToListAsync();

			// 获取拥有技术员、业务员和研发工程师角色的用户
			var roleKeyList = new List<string> { RoleKeyConsts.After_Sale_Technician, RoleKeyConsts.Sales_Man, RoleKeyConsts.RD_Engineer };
			var roles = await(from a in UnitWork.Find<Role>(x => roleKeyList.Contains(x.RoleKey))
							  join b in UnitWork.Find<Relevance>(null) on a.Id equals b.SecondId
							  select new { a.RoleKey, b.FirstId }).ToListAsync();
			var userTypeDict = roles.GroupBy(x => x.RoleKey).ToDictionary(x => x.Key, x => x.Select(y => y.FirstId).ToList());
			userTypeDict.TryGetValue(RoleKeyConsts.After_Sale_Technician, out var technicianUserIds);
			technicianUserIds ??= new List<string>();
			userTypeDict.TryGetValue(RoleKeyConsts.Sales_Man, out var salesmenUserIds);
			salesmenUserIds ??= new List<string>();
			userTypeDict.TryGetValue(RoleKeyConsts.RD_Engineer, out var rdEngineerUserIds);
			rdEngineerUserIds ??= new List<string>();
			foreach (var userId in userIds)
			{
				var abilityList = new List<SkillAbilityDto>();

				if (rdEngineerUserIds.Contains(userId))
				{
					var rdSkill = GetRDEngineerSkill(jobAbilityConfigs, rdEngineers, userId);
					rdSkill.ForEach(x => x.AbilityCode = string.Empty);
					abilityList.Add(new SkillAbilityDto()
					{
						Ability = AbilityHelper.CombinAbilityCodes(rdSkill),
					});
				}
				else
				{
					if (technicianUserIds.Contains(userId))
					{
						var technician = GetTechSkill(jobAbilityConfigs, technicians, userId);
						abilityList.Add(new SkillAbilityDto()
						{
							Ability = AbilityHelper.CombinAbilityCodes(technician),
						});
					}

					if (salesmenUserIds.Contains(userId))
					{
						var salesman = from jobAbility in jobAbilityConfigs.Where(x => x.JobPosition == (int)JobPosition.Salesman)
									   join techSkill in salesmans.Where(x => x.UserId == userId)
									   on jobAbility.Id equals techSkill.JobAbilityId into techSkills
									   from techSkill in techSkills.DefaultIfEmpty()
									   orderby jobAbility.SortNum
									   select new AbilityCodeListDto
									   {
										   AbilityCode = jobAbility.AbilityCode,
										   AbilityValue = techSkill != null ? techSkill.JobAbilityGrade : "0",
										   AbilityType = (AbilityType)jobAbility.AbilityType
									   };
						abilityList.Add(new SkillAbilityDto()
						{
							Ability = AbilityHelper.CombinAbilityCodes(salesman.ToList()),
						});
					}
				}

				// 技术员技能在别的模块还有使用，后续需要优化
				var ability = abilityList.FirstOrDefault()?.Ability;
                
                if(!string.IsNullOrEmpty(ability))
                    userAbilityDict.TryAdd(userId, ability);
			}

            return userAbilityDict;
		}

		/// <summary>
		/// 获取研发工程师技能
		/// </summary>
		/// <param name="jobAbilityConfigs"></param>
		/// <param name="rdEngineers"></param>
		/// <param name="userId"></param>
		/// <returns></returns>
		private static List<AbilityCodeListDto> GetRDEngineerSkill(List<JobAbilityConfig> jobAbilityConfigs, List<RDEngineerSkillAbility> rdEngineers, string userId)
		{
			var rdJobAbilityIds = jobAbilityConfigs.Where(x => x.JobPosition == (int)JobPosition.RDEngineer).Select(x => x.Id);
			var rdSkill = rdEngineers.Where(x => x.UserId == userId).Where(x => rdJobAbilityIds.Contains(x.JobAbilityId)).ToList();
			var rdSkills = new List<AbilityCodeListDto>();
			foreach (var skill in rdSkill)
			{
				var config = jobAbilityConfigs.Find(x => x.Id == skill.JobAbilityId);
				if (config != null)
				{
					var dto = new AbilityCodeListDto
					{
						AbilityCode = config.AbilityCode,
						AbilityValue = skill.JobAbilityGrade,
						AbilityType = (AbilityType)config.AbilityType
					};
					rdSkills.Add(dto);
				}
			}
			return rdSkills.OrderBy(x => x.AbilityCode).ToList();
		}

		/// <summary>
		/// 获取技术员技能
		/// </summary>
		/// <param name="techJobAbility"></param>
		/// <param name="technicians"></param>
		/// <param name="userId"></param>
		/// <returns></returns>
		private static List<AbilityCodeListDto> GetTechSkill(List<JobAbilityConfig> techJobAbility, List<TechnicianSkillAbility> technicians, string userId)
		{
			var techJobAbilityIds = techJobAbility.Where(x => x.JobPosition == (int)JobPosition.Technician).Select(x => x.Id);
			var techSkill = technicians.Where(x => x.UserId == userId).Where(x => techJobAbilityIds.Contains(x.JobAbilityId)).ToList();
			var techSkills = new List<AbilityCodeListDto>();
			foreach (var skill in techSkill)
			{
				var config = techJobAbility.Find(x => x.Id == skill.JobAbilityId);
				if (config != null)
				{
					var dto = new AbilityCodeListDto
					{
						AbilityCode = config.AbilityCode,
						AbilityValue = skill.JobAbilityGrade,
						AbilityType = (AbilityType)config.AbilityType
					};
					techSkills.Add(dto);
				}
			}
			return techSkills.OrderBy(x => x.AbilityCode).ToList();
		}

		/// <summary>
		/// 获取岗位技能设置
		/// </summary>
		/// <returns></returns>
		public async Task<JobAbilitySet> GetJobAbilitySet()
        {
            JobAbilitySet jobAbilitySet = new JobAbilitySet();

            var jobLabelCategoryIds = new List<string> 
            {
                BusinessLabelHelper.TRAIN_POST_LABEL_CATEGORY,
                BusinessLabelHelper.TRAIN_GRADE_LABEL_CATEGORY,
                BusinessLabelHelper.TRAIN_SKILL_LABEL_CATEGORY,
            };
            var jobLabels = await UnitWork.Find<MDBusinessLabel>(p => jobLabelCategoryIds.Contains(p.CategoryId) && !p.IsDelete).Select(p => new { p.Id, p.Name,p.Sort,p.CategoryId}).ToListAsync();

			var postLabels = jobLabels.FindAll(p => p.CategoryId == BusinessLabelHelper.TRAIN_POST_LABEL_CATEGORY).OrderBy(p => p.Sort).ToList();
            var gradeLabels = jobLabels.FindAll(p => p.CategoryId == BusinessLabelHelper.TRAIN_GRADE_LABEL_CATEGORY).OrderBy(p => p.Sort).ToList();
            var skillLabels = jobLabels.FindAll(p => p.CategoryId == BusinessLabelHelper.TRAIN_SKILL_LABEL_CATEGORY).OrderBy(p => p.Sort).ToList();

            jobAbilitySet.PostLabels = postLabels.Select(p => new DtoJobLabel() { Id = p.Id, Name = p.Name,Sort = p.Sort }).ToList();
			jobAbilitySet.GradeLabels = gradeLabels.Select(p => new DtoJobLabel() { Id = p.Id, Name = p.Name, Sort = p.Sort }).ToList();
			jobAbilitySet.SkillLabels = skillLabels.Select(p => new DtoJobLabel() { Id = p.Id, Name = p.Name, Sort = p.Sort }).ToList();

			//岗位技能主键配置
			var jobAbilityConfigs = await UnitWork.Find<JobAbilityConfig>(t => t.Status == (int)AbilityStatus.启用)
                    .OrderBy(t => t.SortNum).ToListAsync();
			jobAbilitySet.AllSelect = new List<JobAbilitySelect>();
			//技术员
			var technicianAbilitys = jobAbilityConfigs.FindAll(t => t.JobPosition == (int)JobPosition.Technician);
            technicianAbilitys.ForEach(ability =>
            {
                jobAbilitySet.TechnicianSelect.Add(new JobAbilitySelect()
                {
                    JobAbilityId = ability.Id,
                    AbilityCode = ability.AbilityCode,
                    AbilityType = ability.AbilityType,
                    SortNum = ability.SortNum
                });
            });

            //业务员
            var salesmanAbilitys = jobAbilityConfigs.FindAll(t => t.JobPosition == (int)JobPosition.Salesman);
            salesmanAbilitys.ForEach(ability =>
            {
                jobAbilitySet.SalesmanSelect.Add(new JobAbilitySelect()
                {
                    JobAbilityId = ability.Id,
                    AbilityCode = ability.AbilityCode,
                    AbilityType = ability.AbilityType,
                    SortNum = ability.SortNum
                });
            });

            //研发工程师
            var rdEngineerAbilitys = jobAbilityConfigs.FindAll(t => t.JobPosition == (int)JobPosition.RDEngineer);
            rdEngineerAbilitys.ForEach(ability =>
            {
                jobAbilitySet.RDEngineerSelect.Add(new JobAbilitySelect()
                {
                    JobAbilityId = ability.Id,
                    AbilityCode = ability.AbilityCode,
                    AbilityType = ability.AbilityType,
                    SortNum = ability.SortNum
                });
            });

            return jobAbilitySet;
        }

        /// <summary>
        /// 批量获取用户技能等级
        /// </summary>
        /// <param name="req">请求参数</param>
        /// <returns>技能等级列表</returns>
        public async Task<Response<List<UserSkillGradeResp>>> GetSkillGradeByPassportIds(GetSkillGradeByPassportIdsReq req)
        {
            // 1. 参数验证
            if (req.PassportIds == null || !req.PassportIds.Any())
            {
                return new Response<List<UserSkillGradeResp>> { Code = 500, Message = "PassportIds不能为空" };
            }

            // 2. Type → JobPosition 映射
            int jobPosition;
            switch (req.Type?.ToLower())
            {
                case "technician":
                    jobPosition = 1;
                    break;
                case "salesman":
                    jobPosition = 2;
                    break;
                case "rdengineer":
                    jobPosition = 3;
                    break;
                default:
                    return new Response<List<UserSkillGradeResp>> { Code = 500, Message = "Type参数错误，必须是：rdEngineer、salesman 或 technician" };
            }

            // 3. 批量查询 AppUserMap，获得 passportId → userId 映射
            var userMaps = await UnitWork.Find<OpenAuth.Repository.Domain.AppUserMap>(p => req.PassportIds.Contains(p.PassPortId.Value))
                .Select(p => new { p.PassPortId, p.UserID })
                .ToListAsync();

            if (!userMaps.Any())
            {
                return new Response<List<UserSkillGradeResp>> { Code = 200, Message = "操作成功", Result = new List<UserSkillGradeResp>() };
            }

            var userIdToPassportId = userMaps.ToDictionary(x => x.UserID, x => x.PassPortId.Value);
            var userIds = userIdToPassportId.Keys.ToList();

            // 4. 查询 JobAbilityConfig，获得 configId → AbilityRemark 映射
            var abilityConfigs = await UnitWork.Find<OpenAuth.Repository.Domain.MD.Train.JobAbilityConfig>(
                    p => p.JobPosition == jobPosition && p.Status == 1)
                .Select(p => new { p.Id, p.AbilityRemark })
                .ToListAsync();

            if (!abilityConfigs.Any())
            {
                return new Response<List<UserSkillGradeResp>> { Code = 200, Message = "操作成功", Result = new List<UserSkillGradeResp>() };
            }

            var configIdToAbilityRemark = abilityConfigs.ToDictionary(x => x.Id, x => x.AbilityRemark);
            var configIds = configIdToAbilityRemark.Keys.ToList();

            // 5. 按 type 选对应技能表，查询用户技能记录
            List<(string UserId, string JobAbilityId, string JobAbilityGrade)> skillRecords;
            switch (req.Type.ToLower())
            {
                case "technician":
                    var techSkills = await UnitWork.Find<OpenAuth.Repository.Domain.Technicians.TechnicianSkillAbility>(
                            p => userIds.Contains(p.UserId) && configIds.Contains(p.JobAbilityId))
                        .Select(p => new { p.UserId, p.JobAbilityId, p.JobAbilityGrade })
                        .ToListAsync();
                    skillRecords = techSkills.Select(x => (x.UserId, x.JobAbilityId, x.JobAbilityGrade)).ToList();
                    break;
                case "salesman":
                    var salesSkills = await UnitWork.Find<OpenAuth.Repository.Domain.Technicians.SalesmanSkillAbility>(
                            p => userIds.Contains(p.UserId) && configIds.Contains(p.JobAbilityId))
                        .Select(p => new { p.UserId, p.JobAbilityId, p.JobAbilityGrade })
                        .ToListAsync();
                    skillRecords = salesSkills.Select(x => (x.UserId, x.JobAbilityId, x.JobAbilityGrade)).ToList();
                    break;
                case "rdengineer":
                    var rdSkills = await UnitWork.Find<OpenAuth.Repository.Domain.Technicians.RDEngineerSkillAbility>(
                            p => userIds.Contains(p.UserId) && configIds.Contains(p.JobAbilityId))
                        .Select(p => new { p.UserId, p.JobAbilityId, p.JobAbilityGrade })
                        .ToListAsync();
                    skillRecords = rdSkills.Select(x => (x.UserId, x.JobAbilityId, x.JobAbilityGrade)).ToList();
                    break;
                default:
                    return new Response<List<UserSkillGradeResp>> { Code = 500, Message = "Type参数错误" };
            }

            // 6. 组装响应
            var result = new List<UserSkillGradeResp>();
            foreach (var (userId, jobAbilityId, jobAbilityGrade) in skillRecords)
            {

                if (!userIdToPassportId.TryGetValue(userId, out int passportId)) continue;
                if (!configIdToAbilityRemark.TryGetValue(jobAbilityId, out string skillCode)) continue;

                // 等级转换
                string grade;
                if (req.Type.ToLower() == "rdengineer")
                {
                    // rdEngineer: A→"1", B→"2", C→"3"
                    if (!string.IsNullOrEmpty(jobAbilityGrade))
                    {
                        char gradeChar = char.ToUpper(jobAbilityGrade[0]);
                        if (char.IsLetter(gradeChar))
                        {
                            int gradeNum = gradeChar - 'A' + 1;
                            grade = gradeNum.ToString();
                        }
                        else
                        {
                            grade = jobAbilityGrade;
                        }
                    }
                    else
                    {
                        grade = "";
                    }
                }
                else
                {
                    // salesman/technician: 直接返回 JobAbilityGrade
                    grade = jobAbilityGrade ?? "";
                }

                result.Add(new UserSkillGradeResp
                {
                    PassportId = passportId,
                    SkillCode = skillCode,
                    Grade = grade
                });
            }

            return new Response<List<UserSkillGradeResp>> { Code = 200, Message = "操作成功", Result = result };
        }

        /// <summary>
        /// 获取技能变更历史记录
        /// </summary>
        /// <param name="userId">用户ID</param>
        /// <param name="jobPosition">岗位类型</param>
        /// <returns>变更历史列表</returns>
        public async Task<Response<List<AbilityChangeHistoryResp>>> GetAbilityChangeHistory(string userId, JobPosition jobPosition)
        {
            var histories = await UnitWork.Find<JobAbilityChangeHistory>(
                x => x.UserId == userId && x.JobPosition == (int)jobPosition)
                .OrderBy(x => x.CreateTime)
                .ToListAsync();

            if (!histories.Any())
                return new Response<List<AbilityChangeHistoryResp>> { Code = 200, Message = "操作成功", Result = new List<AbilityChangeHistoryResp>() };

            var historyIds = histories.Select(x => x.Id).ToList();
            var allItems = await UnitWork.Find<JobAbilityChangeHistoryItem>(
                x => historyIds.Contains(x.ChangeHistoryId))
                .ToListAsync();

            var itemsGrouped = allItems.GroupBy(x => x.ChangeHistoryId)
                .ToDictionary(g => g.Key, g => g.OrderBy(i => i.AbilityCode).ToList());

            var result = new List<AbilityChangeHistoryResp>();
            List<JobAbilityChangeHistoryItem> previousItems = null;
            // 技术员 A/B/C 为初/中/高级，互斥存在
            var techLevelCodes = new HashSet<string> { "A", "B", "C" };
            bool isTechnician = jobPosition == JobPosition.Technician;

            foreach (var history in histories)
            {
                itemsGrouped.TryGetValue(history.Id, out var currentItems);
                currentItems = currentItems ?? new List<JobAbilityChangeHistoryItem>();

                string changeContent;
                if (previousItems == null)
                    changeContent = BuildInitialChangeContent(currentItems);
                else if (isTechnician)
                    changeContent = BuildTechnicianChangeContent(previousItems, currentItems, techLevelCodes);
                else
                    changeContent = BuildGeneralChangeContent(previousItems, currentItems);

                result.Add(new AbilityChangeHistoryResp
                {
                    CreateId = history.CreateId,
                    ChangeContent = changeContent,
                    Reason = history.Reason,
                    CreateName = history.CreateName,
                    CreateTime = history.CreateTime
                });

                previousItems = currentItems;
            }

            result.Reverse();
            return new Response<List<AbilityChangeHistoryResp>> { Code = 200, Message = "操作成功", Result = result };
        }

        /// <summary>
        /// 构建第一条记录的初始状态描述
        /// </summary>
        private string BuildInitialChangeContent(List<JobAbilityChangeHistoryItem> currentItems)
        {
            return string.Join(", ", currentItems.Select(
                item => $"{item.AbilityRemark}：{item.AbilityCode}{item.AbilityValue}"));
        }

        /// <summary>
        /// 构建技术员的技能变更内容
        /// </summary>
        private string BuildTechnicianChangeContent(List<JobAbilityChangeHistoryItem> previousItems,
            List<JobAbilityChangeHistoryItem> currentItems, HashSet<string> techLevelCodes)
        {
            var changes = new List<string>();
            var curLevel = currentItems.Find(i => techLevelCodes.Contains(i.AbilityCode));
            var prevLevel = previousItems.Find(i => techLevelCodes.Contains(i.AbilityCode));

            if (curLevel != null && prevLevel != null)
            {
                if (curLevel.AbilityCode != prevLevel.AbilityCode)
                {
                    // 级别变化：初级 -> 高级
                    changes.Add($"{prevLevel.AbilityRemark} 变更为 {curLevel.AbilityRemark}，{prevLevel.AbilityCode}{prevLevel.AbilityValue} -> {curLevel.AbilityCode}{curLevel.AbilityValue}");
                }
                else if (curLevel.AbilityValue != prevLevel.AbilityValue)
                {
                    // 同级等级变化
                    changes.Add($"{curLevel.AbilityRemark}：{curLevel.AbilityCode}{prevLevel.AbilityValue} -> {curLevel.AbilityCode}{curLevel.AbilityValue}");
                }
            }
            else if (curLevel != null && prevLevel == null)
            {
                changes.Add($"{curLevel.AbilityRemark}：{curLevel.AbilityCode}{curLevel.AbilityValue}");
            }

            // U(院校技能) 按通用逻辑处理
            foreach (var item in currentItems.Where(i => !techLevelCodes.Contains(i.AbilityCode)))
            {
                var prevItem = previousItems.Find(p => p.AbilityCode == item.AbilityCode);
                if (prevItem == null || prevItem.AbilityValue != item.AbilityValue)
                {
                    var oldValue = prevItem != null
                        ? $"{item.AbilityCode}{prevItem.AbilityValue}"
                        : "无";
                    changes.Add($"{item.AbilityRemark}变更：{oldValue} -> {item.AbilityCode}{item.AbilityValue}");
                }
            }

            return changes.Any() ? string.Join(", ", changes) : "无变更";
        }

        /// <summary>
        /// 构建销售员/研发工程师的通用技能变更内容
        /// </summary>
        private string BuildGeneralChangeContent(List<JobAbilityChangeHistoryItem> previousItems,
            List<JobAbilityChangeHistoryItem> currentItems)
        {
            var changes = new List<string>();
            foreach (var item in currentItems)
            {
                var prevItem = previousItems.Find(p => p.AbilityCode == item.AbilityCode);
                if (prevItem == null || prevItem.AbilityValue != item.AbilityValue)
                {
                    var oldValue = prevItem != null
                        ? $"{item.AbilityCode}{prevItem.AbilityValue}"
                        : "无";
                    changes.Add($"{item.AbilityRemark}变更：{oldValue} -> {item.AbilityCode}{item.AbilityValue}");
                }
            }
            return changes.Any() ? string.Join(", ", changes) : "无变更";
        }
    }
}
